import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from ff_iii_luciferin.domain.models import TxType

from api.deps_services import get_firefly_client
from services.guards import require_active_user
from tests.services.test_csv_reader import REQUIRED_HEADER
from tests.services.test_reconciliation import (
    ASSET,
    DAY,
    HEADER,
    statement,
    transaction,
)


@pytest.fixture
def reconciliation_client(client, monkeypatch):
    firefly = MagicMock()
    firefly.get_transaction = AsyncMock(return_value=transaction())
    firefly.update_transaction = AsyncMock()

    async def fetch(*args, **kwargs):
        return [transaction()], SimpleNamespace(multipart=0, invalid=0)

    monkeypatch.setattr(
        "services.reconciliation.service.fetch_transactions_with_stats", fetch
    )
    client.app.dependency_overrides[require_active_user] = lambda: "user"
    client.app.dependency_overrides[get_firefly_client] = lambda: firefly
    return client, firefly


def fields(**changes):
    return {
        "account_id": "1",
        "date_from": DAY.isoformat(),
        "date_to": DAY.isoformat(),
        "tolerance_days": "0",
    } | changes


def upload(client, path="preview", *, content=None, **changes):
    return client.post(
        f"/api/reconciliation/{path}",
        data=fields(**changes),
        files={
            "file": (
                "bank.csv",
                statement() if content is None else content,
                "text/csv",
            )
        },
    )


@pytest.fixture
def group_client(reconciliation_client, monkeypatch):
    client, firefly = reconciliation_client
    transactions = {}

    async def fetch(*args, **kwargs):
        return list(transactions.values()), SimpleNamespace(multipart=0, invalid=0)

    async def update(tx_id, update, **kwargs):
        assert update.reconciled is True
        assert kwargs == {"apply_rules": False, "fire_webhooks": False}
        transactions[tx_id] = replace(transactions[tx_id], reconciled=True)

    monkeypatch.setattr(
        "services.reconciliation.service.fetch_transactions_with_stats", fetch
    )
    firefly.get_transaction.side_effect = transactions.__getitem__
    firefly.update_transaction.side_effect = update
    return client, firefly, transactions


def apply_group(client, content, preview, group, **changes):
    return upload(
        client,
        "apply",
        content=content,
        revision=preview["revision"],
        selected_group_ids=json.dumps([group["id"]]),
        statement_complete="true",
        **changes,
    )


@pytest.mark.parametrize(
    "bank_titles, firefly_titles, exact_count",
    [
        (["VEMAT", "VEMAT"], ["VEMAT", "VEMAT"], 2),
        (["Fikcyjny zakup w sieci"], ["Płatność BLIK w internecie"], 0),
    ],
)
def test_apply_balanced_group_without_individual_description_matches(
    group_client, bank_titles, firefly_titles, exact_count
):
    client, firefly, transactions = group_client
    content = statement(*(f"16-11-2026;-30,04;PLN;{title}" for title in bank_titles))
    for tx_id, title in enumerate(firefly_titles, start=1):
        transactions[tx_id] = transaction(tx_id, description=title)

    response = upload(client, content=content)
    assert response.status_code == 200
    preview = response.json()
    [group] = preview["reconciliation_groups"]
    assert len(group["bank"]) == len(group["firefly"]) == len(firefly_titles)
    assert group["exact_description_count"] == exact_count
    assert group["eligible_ids"] == list(transactions)
    assert {row["status"] for row in preview["rows"]} == {"review"}

    applied = apply_group(client, content, preview, group)
    assert applied.status_code == 200
    assert applied.json()["outcomes"] == [
        {"id": tx_id, "status": "reconciled"} for tx_id in transactions
    ]
    assert [
        call.args[0] for call in firefly.update_transaction.await_args_list
    ] == list(transactions)
    retry = apply_group(client, content, preview, group)
    assert retry.status_code == 200
    assert {item["status"] for item in retry.json()["outcomes"]} == {
        "already_reconciled"
    }
    assert firefly.update_transaction.await_count == len(firefly_titles)


@pytest.mark.parametrize("change_after_preview", [False, True])
def test_apply_rejects_surplus_group_or_changed_count(
    group_client, change_after_preview
):
    client, firefly, transactions = group_client
    content = statement()
    transactions[1] = transaction()
    if not change_after_preview:
        transactions[2] = transaction(2)

    preview = upload(client, content=content).json()
    [group] = preview["reconciliation_groups"]
    assert group["eligible_ids"] == ([1] if change_after_preview else [])
    assert len(group["firefly"]) == (1 if change_after_preview else 2)
    if change_after_preview:
        transactions[2] = transaction(2)

    response = apply_group(client, content, preview, group)
    assert response.status_code == (409 if change_after_preview else 422)
    firefly.get_transaction.assert_not_awaited()
    firefly.update_transaction.assert_not_awaited()


def test_apply_rejects_entire_mixed_group_containing_a_transfer(group_client):
    client, firefly, transactions = group_client
    transactions[1] = transaction()
    transactions[2] = transaction(
        2, type=TxType.TRANSFER, destination_account=replace(ASSET, id=2, iban=None)
    )
    content = statement(*["16-11-2026;-30,04;PLN;Fikcyjny zakup"] * 2)
    preview = upload(client, content=content).json()
    [group] = preview["reconciliation_groups"]
    assert group["contains_transfer"]
    assert group["eligible_ids"] == []
    assert apply_group(client, content, preview, group).status_code == 422
    firefly.update_transaction.assert_not_awaited()


def test_group_and_individual_selections_update_the_same_transaction_once(group_client):
    client, firefly, transactions = group_client
    transactions[1] = transaction()
    content = statement()
    preview = upload(client, content=content).json()
    [group] = preview["reconciliation_groups"]
    assert (
        apply_group(client, content, preview, group, selected_ids="[1]").status_code
        == 200
    )
    firefly.update_transaction.assert_awaited_once()


def test_group_larger_than_a_normal_batch_can_be_submitted_in_full(group_client):
    client, firefly, transactions = group_client
    content = statement(*["16-11-2026;-30,04;PLN;Fikcyjna operacja"] * 101)
    transactions.update({tx_id: transaction(tx_id) for tx_id in range(1, 102)})
    preview = upload(client, content=content).json()
    [group] = preview["reconciliation_groups"]

    response = apply_group(client, content, preview, group)
    assert response.status_code == 200
    assert len(response.json()["outcomes"]) == 101
    assert firefly.update_transaction.await_count == 101


def test_preview_requires_authentication(client):
    assert upload(client).status_code == 401
    assert upload(client, "statement-period").status_code == 401
    assert (
        upload(client, "apply", revision="a" * 64, selected_ids="[1]").status_code
        == 401
    )


@pytest.mark.parametrize(
    "metadata, source, start, end",
    [
        (
            "Wyciąg za okres: 01-11-2026 – 30-11-2026",
            "metadata",
            "2026-11-01",
            "2026-11-30",
        ),
        ('"Zakres dat";2026-11-01;2026-11-30', "metadata", "2026-11-01", "2026-11-30"),
        (
            "Data od;01.11.2026;Data do;30.11.2026",
            "metadata",
            "2026-11-01",
            "2026-11-30",
        ),
        ("Okres;01-11-2026;13-11-2026", "transactions", "2026-11-12", "2026-11-18"),
        (
            "Wygenerowano 01-11-2026;30-11-2026",
            "transactions",
            "2026-11-12",
            "2026-11-18",
        ),
        ("", "transactions", "2026-11-12", "2026-11-18"),
    ],
)
def test_statement_period_prefers_valid_metadata_over_booking_date_bounds(
    reconciliation_client, metadata, source, start, end
):
    client, _ = reconciliation_client
    content = (
        (metadata + "\n" if metadata else "")
        + HEADER.strip()
        + ";Data księgowania\n"
        + "15-11-2026;-30,04;PLN;Fikcyjny zakup;18-11-2026\n"
        + "08-11-2026;-10,00;PLN;Fikcyjny zakup;12-11-2026\n"
    ).encode()
    response = upload(client, "statement-period", content=content)
    assert response.status_code == 200
    assert response.json() == {"date_from": start, "date_to": end, "source": source}


def test_group_apply_requires_statement_completeness_after_preview(group_client):
    client, firefly, transactions = group_client
    transactions[1] = transaction()
    preview = upload(client).json()
    [group] = preview["reconciliation_groups"]
    response = upload(
        client,
        "apply",
        revision=preview["revision"],
        selected_group_ids=json.dumps([group["id"]]),
    )
    assert response.status_code == 422
    assert "Confirm the statement" in response.json()["detail"]
    firefly.update_transaction.assert_not_awaited()


def test_statement_period_reports_invalid_booking_date_by_file_line(
    reconciliation_client,
):
    client, _ = reconciliation_client
    content = (
        "Metadata\n" + HEADER.strip() + ";Data księgowania\n"
        "16-11-2026;-5,00;PLN;Fikcyjny zakup;Niewłaściwa data\n"
    ).encode()
    response = upload(client, "statement-period", content=content)
    assert response.status_code == 422
    assert response.json()["detail"] == (
        'Invalid value in "Data księgowania" at CSV file line 3 (record 1)'
    )


def test_preview_and_invalid_selection(reconciliation_client):
    client, firefly = reconciliation_client
    response = upload(client)
    assert response.status_code == 200
    preview = response.json()
    assert preview["rows"][0]["status"] == "matched"
    invalid = upload(
        client, "apply", revision=preview["revision"], selected_ids="[999]"
    )
    assert invalid.status_code == 422
    firefly.update_transaction.assert_not_awaited()


def test_apply_rejects_normalized_description_suggestion(
    reconciliation_client, monkeypatch
):
    client, firefly = reconciliation_client
    content = statement("16-11-2026;-30,04;PLN;Faktura 123 POZNAŃ PL")

    async def fetch(*args, **kwargs):
        tx = transaction(description="Transakcja kartą debetową, Faktura 123")
        return [tx], SimpleNamespace(multipart=0, invalid=0)

    monkeypatch.setattr(
        "services.reconciliation.service.fetch_transactions_with_stats", fetch
    )
    response = upload(client, content=content)
    assert response.status_code == 200
    preview = response.json()
    row = preview["rows"][0]
    assert row["review_reason"] == "description_normalization"
    assert row["candidates"][0]["can_mark"] is False
    rejected = upload(
        client,
        "apply",
        content=content,
        revision=preview["revision"],
        selected_ids="[1]",
    )
    assert rejected.status_code == 422
    firefly.get_transaction.assert_not_awaited()
    firefly.update_transaction.assert_not_awaited()


def test_preview_accepts_alior_quotes_in_counterparty_names(reconciliation_client):
    client, firefly = reconciliation_client
    content = (
        f"Export metadata\n{REQUIRED_HEADER}\n"
        '16-11-2026;-30,04;-30,04;Sender;"Example Company" Ltd;faktura 123;PLN;PLN;;\n'
        '16-11-2026;5,00;5,00;"Example Company" Ltd;Recipient;Refund;PLN;PLN;;\n'
    ).encode()
    response = client.post(
        "/api/reconciliation/preview",
        data=fields(),
        files={"file": ("bank.csv", content, "text/csv")},
    )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [(row["status"], row["bank"]["amount"]) for row in rows] == [
        ("matched", "-30.04"),
        ("bank_only", "5.00"),
    ]
    firefly.update_transaction.assert_not_awaited()


def test_multipart_preview_then_apply_marks_and_verifies(reconciliation_client):
    client, firefly = reconciliation_client
    preview = upload(client).json()
    firefly.get_transaction.side_effect = [
        transaction(),
        transaction(),
        transaction(reconciled=True),
    ]
    response = upload(client, "apply", revision=preview["revision"], selected_ids="[1]")
    assert response.status_code == 200
    assert response.json() == {"outcomes": [{"id": 1, "status": "reconciled"}]}
    args, kwargs = firefly.update_transaction.await_args
    assert args[0] == 1 and args[1].reconciled is True
    assert kwargs == {"apply_rules": False, "fire_webhooks": False}


@pytest.mark.parametrize(
    "changes",
    [
        {"date_from": "2026-11-20"},
        {"account_id": "0"},
        {"tolerance_days": "3"},
        {"date_from": "2020-01-01"},
    ],
)
def test_bad_filters(reconciliation_client, changes):
    assert upload(reconciliation_client[0], **changes).status_code == 422


def test_stale_revision_and_bounded_upload(reconciliation_client):
    client, firefly = reconciliation_client
    assert (
        upload(client, "apply", revision="0" * 64, selected_ids="[1]").status_code
        == 409
    )
    response = client.post(
        "/api/reconciliation/preview",
        data=fields(),
        files={"file": ("big.csv", b"a" * (2 * 1024 * 1024 + 1))},
    )
    assert response.status_code == 413
    firefly.update_transaction.assert_not_awaited()
