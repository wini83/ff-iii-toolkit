import json
from dataclasses import replace
from decimal import Decimal
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from ff_iii_luciferin.api import FireflyAPIError
from ff_iii_luciferin.domain.models import (
    AccountType,
    SimplifiedAccount,
    SimplifiedAccountRef,
    TxType,
)

from api.deps_services import get_firefly_client
from services.guards import require_active_user
from tests.services.test_reconciliation import transaction

PREFIX = "/api/statement-counterparties"
OWN, SHOP, PAYER, SAVINGS = ("1" * 26, "2" * 26, "3" * 26, "4" * 26)
ASSET = SimplifiedAccountRef(1, "Checking", AccountType.ASSET, "PL" + OWN)
UNKNOWN_EXPENSE = SimplifiedAccountRef(
    8, "(unknown destination account)", AccountType.EXPENSE
)
UNKNOWN_REVENUE = SimplifiedAccountRef(
    9, "(unknown source account)", AccountType.REVENUE
)
HEADER = (
    "Data transakcji;Data księgowania;Kwota w walucie rachunku;Waluta rachunku;"
    "Szczegóły transakcji;Numer rachunku nadawcy;Numer rachunku odbiorcy\n"
)


def csv(*rows):
    return ("Metadata\n" + HEADER + "\n".join(rows)).encode()


OUT = f"15-11-2026;16-11-2026;-30,04;PLN;Invoice;{OWN};{SHOP}"
IN = f"15-11-2026;16-11-2026;12,00;PLN;Refund;{PAYER};{OWN}"
CARD = "15-11-2026;16-11-2026;-30,04;PLN;SHOP K.1 WARSZAWA PL;;"


@pytest.fixture
def counterparty_client(client, monkeypatch):
    owner = uuid4()
    accounts = [
        SimplifiedAccount(1, "Checking", "asset", iban="PL" + OWN, active=True),
        SimplifiedAccount(2, "Shop", "expense", account_number=SHOP, active=True),
        SimplifiedAccount(3, "Payer", "revenue", iban="PL" + PAYER, active=True),
        SimplifiedAccount(4, "Savings", "asset", iban="PL" + SAVINGS, active=True),
    ]
    txs = {
        1: transaction(
            description="Invoice",
            source_account=ASSET,
            destination_account=UNKNOWN_EXPENSE,
        ),
        2: transaction(
            2,
            amount=Decimal("12"),
            description="Refund",
            type=TxType.DEPOSIT,
            source_account=UNKNOWN_REVENUE,
            destination_account=ASSET,
        ),
    }
    firefly = MagicMock()
    firefly.fetch_accounts = AsyncMock(return_value=accounts)
    firefly.get_transaction = AsyncMock(side_effect=lambda tx_id: txs[tx_id])

    async def update(tx_id, patch, **kwargs):
        tx = txs[tx_id]
        if patch.destination_id is not None:
            target = next(
                account for account in accounts if account.id == patch.destination_id
            )
            tx = replace(
                tx,
                destination_account=SimplifiedAccountRef(
                    target.id, target.name, AccountType.EXPENSE
                ),
            )
        if patch.source_id == 3:
            tx = replace(
                tx, source_account=SimplifiedAccountRef(3, "Payer", AccountType.REVENUE)
            )
        txs[tx_id] = tx
        return tx

    firefly.update_transaction = AsyncMock(side_effect=update)

    async def fetch(*args, **kwargs):
        return list(txs.values()), SimpleNamespace(multipart=0, invalid=0)

    monkeypatch.setattr(
        "services.statement_counterparties.fetch_transactions_with_stats", fetch
    )
    client.app.dependency_overrides[require_active_user] = lambda: owner
    client.app.dependency_overrides[get_firefly_client] = lambda: firefly
    return client, firefly, txs


def upload(client, path, content, **fields):
    return client.post(
        f"{PREFIX}/{path}",
        data={"account_id": "1", **fields},
        files={"file": ("statement.csv", content, "text/csv")},
    )


def completed_job(client, job_id):
    deadline = monotonic() + 2
    while monotonic() < deadline:
        response = client.get(f"{PREFIX}/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {"done", "failed"}:
            return job
        sleep(0.01)
    pytest.fail("Background job did not finish")


def test_preview_apply_and_owner_scoped_status(counterparty_client):
    client, firefly, txs = counterparty_client
    content = csv(OUT, IN, OUT.replace("-30,04", "-5,00").replace(SHOP, SAVINGS))
    preview = upload(client, "preview", content)
    assert preview.status_code == 200
    plan = preview.json()
    assert [row["status"] for row in plan["rows"]] == ["ready", "ready", "review"]
    assert plan["rows"][2]["proposed_account"] == "Savings"
    firefly.update_transaction.assert_not_awaited()

    start = upload(client, "apply", content, revision=plan["revision"])
    assert start.status_code == 202
    job = completed_job(client, start.json()["id"])
    assert (job["status"], job["total"], job["applied"], job["failed"]) == (
        "done",
        2,
        2,
        0,
    )
    assert txs[1].destination_account.id == 2
    assert txs[2].source_account.id == 3
    for call in firefly.update_transaction.await_args_list:
        assert call.kwargs == {"apply_rules": False, "fire_webhooks": False}
    assert (
        upload(client, "apply", content, revision=plan["revision"]).status_code == 409
    )
    client.app.dependency_overrides[require_active_user] = uuid4
    assert client.get(f"{PREFIX}/jobs/{job['id']}").status_code == 404


def test_ambiguous_and_stale_previews_cannot_write(counterparty_client):
    client, firefly, txs = counterparty_client
    repeated = upload(client, "preview", csv(OUT, OUT)).json()
    assert (repeated["ready"], repeated["review"]) == (0, 2)

    content = csv(OUT)
    plan = upload(client, "preview", content).json()
    txs[1] = replace(txs[1], description="Different invoice")
    assert (
        upload(client, "apply", content, revision=plan["revision"]).status_code == 409
    )
    firefly.update_transaction.assert_not_awaited()


@pytest.mark.parametrize(
    ("before_preview", "before_apply"),
    [(True, True), (False, True), (None, None)],
)
def test_counterparty_update_preserves_reconciliation_and_amount(
    counterparty_client, before_preview, before_apply
):
    client, firefly, txs = counterparty_client
    content = csv(OUT, IN)
    previous = {id_: replace(tx, reconciled=before_preview) for id_, tx in txs.items()}
    txs.update(previous)

    plan = upload(client, "preview", content).json()
    assert plan["ready"] == 2
    txs.update({id_: replace(tx, reconciled=before_apply) for id_, tx in txs.items()})

    start = upload(client, "apply", content, revision=plan["revision"])
    assert start.status_code == 202
    job = completed_job(client, start.json()["id"])
    assert (job["status"], job["applied"]) == ("done", 2)
    assert txs[1].destination_account.id == 2
    assert txs[2].source_account.id == 3
    for id_, tx in txs.items():
        assert tx.reconciled is before_apply
        assert tx.amount == previous[id_].amount
    for call in firefly.update_transaction.await_args_list:
        assert call.args[1].reconciled is None


def test_unverified_write_stops_the_batch(counterparty_client):
    client, firefly, _ = counterparty_client
    content = csv(OUT, IN)
    plan = upload(client, "preview", content).json()
    firefly.update_transaction.side_effect = FireflyAPIError("Timeout")

    start = upload(client, "apply", content, revision=plan["revision"])
    assert start.status_code == 202
    job = completed_job(client, start.json()["id"])
    assert (job["status"], job["total"], job["applied"], job["failed"]) == (
        "failed",
        2,
        0,
        1,
    )
    assert "Could not verify the write" in job["results"][0]["reason"]
    firefly.update_transaction.assert_awaited_once()


@pytest.fixture
def card_client(counterparty_client):
    client, firefly, txs = counterparty_client
    accounts = firefly.fetch_accounts.return_value
    accounts[1] = replace(accounts[1], name="Shop K.1", account_number=None)
    txs[1] = replace(
        txs[1], description="Transakcja kartą debetową z CashBack, Shop K.1"
    )
    return client, firefly, txs


def test_numberless_card_uses_merchant_without_requiring_a_known_city(card_client):
    client, firefly, txs = card_client
    content = csv(CARD.replace("WARSZAWA", "WYSOGOTOWO"), IN)
    response = upload(client, "preview", content, single_account_confirmed="true")
    assert response.status_code == 200
    plan = response.json()
    card, incoming = plan["rows"]
    assert (card["status"], card["proposed_account"]) == ("ready", "Shop K.1")
    assert "card merchant" in card["reason"]
    assert incoming["status"] == "ready"
    firefly.update_transaction.assert_not_awaited()

    assert (
        upload(client, "apply", content, revision=plan["revision"]).status_code == 409
    )
    start = upload(
        client,
        "apply",
        content,
        revision=plan["revision"],
        single_account_confirmed="true",
    )
    assert start.status_code == 202
    job = completed_job(client, start.json()["id"])
    assert (job["status"], job["applied"]) == ("done", 2)
    assert txs[1].destination_account.name == "Shop K.1"


@pytest.mark.parametrize(
    ("rows", "confirmed", "reason"),
    [
        ((CARD.replace("K.1", "K.10"), IN), True, "merchant differs"),
        ((CARD, CARD, IN), True, "Multiple transactions"),
        ((CARD,), False, "Could not verify the statement account"),
        ((CARD, IN), False, "Could not verify the statement account"),
        (
            (CARD, IN, OUT.replace(OWN, SAVINGS)),
            True,
            "Could not verify the statement account",
        ),
    ],
)
def test_name_matching_routes_conflicting_or_unverified_rows_to_review(
    card_client, rows, confirmed, reason
):
    client, firefly, _ = card_client
    response = upload(
        client, "preview", csv(*rows), single_account_confirmed=str(confirmed).lower()
    )
    assert response.status_code == 200
    card = response.json()["rows"][0]
    assert card["status"] == "review"
    assert reason in card["reason"]
    firefly.update_transaction.assert_not_awaited()


def test_duplicate_merchant_names_invalidate_a_ready_preview(card_client):
    client, firefly, _ = card_client
    content = csv(CARD, IN)
    fields = {"single_account_confirmed": "true"}
    initial = upload(client, "preview", content, **fields).json()
    assert initial["rows"][0]["status"] == "ready"
    accounts = firefly.fetch_accounts.return_value
    accounts.append(SimplifiedAccount(5, "  SHOP   K.1 ", "expense", active=True))

    assert (
        upload(
            client, "apply", content, revision=initial["revision"], **fields
        ).status_code
        == 409
    )
    row = upload(client, "preview", content, **fields).json()["rows"][0]
    assert row["status"] == "review"
    assert row["choice_group"]
    assert [
        (candidate["id"], candidate["match"]) for candidate in row["candidates"]
    ] == [(2, "merchant"), (5, "merchant")]
    firefly.update_transaction.assert_not_awaited()


def test_partial_account_name_is_only_a_suggestion(card_client):
    client, firefly, _ = card_client
    firefly.fetch_accounts.return_value.append(
        SimplifiedAccount(5, "Shop K.1 Market", "expense", active=True)
    )
    content = csv(CARD.replace("K.1 WARSZAWA", "K.1 MARKET WARSZAWA"))
    plan = upload(client, "preview", content, single_account_confirmed="true").json()
    assert plan["ready"] == 1
    row = plan["rows"][0]
    assert row["proposed_account"] == "Shop K.1"
    assert row["choice_group"] is None
    assert [
        (candidate["id"], candidate["match"]) for candidate in row["candidates"]
    ] == [(2, "merchant"), (5, "prefix")]


def test_unique_full_statement_name_is_ready_without_a_merchant_account(card_client):
    client, firefly, _ = card_client
    accounts = firefly.fetch_accounts.return_value
    accounts[1] = replace(accounts[1], name="Shop K.1 Warszawa PL")
    plan = upload(client, "preview", csv(CARD), single_account_confirmed="true").json()
    assert plan["ready"] == 1
    assert plan["rows"][0]["proposed_account"] == accounts[1].name
    assert plan["rows"][0]["candidates"][0]["match"] == "statement"


def test_one_exact_candidate_choice_applies_to_the_whole_group(card_client):
    client, firefly, txs = card_client
    accounts = firefly.fetch_accounts.return_value
    accounts.extend(
        [
            SimplifiedAccount(5, "Shop K.1 Warszawa PL", "expense", active=True),
            SimplifiedAccount(6, "Shop K.1 Warszawa", "expense", active=True),
        ]
    )
    txs[3] = replace(txs[1], id=3, amount=Decimal("65.60"), transaction_journal_id=103)
    txs[4] = replace(
        txs[1],
        id=4,
        amount=Decimal("22"),
        transaction_journal_id=104,
        destination_account=SimplifiedAccountRef(
            5, accounts[4].name, AccountType.EXPENSE
        ),
    )
    statement = csv(CARD, CARD.replace("-30,04", "-65,60"))
    fields = {"single_account_confirmed": "true"}
    preview = upload(client, "preview", statement, **fields).json()
    assert (preview["ready"], preview["review"]) == (0, 2)
    first, second = preview["rows"]
    group = first["choice_group"]
    assert group and second["choice_group"] == group
    assert [
        (candidate["id"], candidate["match"], candidate["prior_assignments"])
        for candidate in first["candidates"]
    ] == [(5, "statement", 1), (2, "merchant", 0), (6, "prefix", 0)]
    apply_fields = {**fields, "revision": preview["revision"]}
    assert (
        upload(
            client,
            "apply",
            statement,
            **apply_fields,
            account_choices=json.dumps({group: 6}),
        ).status_code
        == 422
    )
    assert upload(client, "apply", statement, **apply_fields).status_code == 422
    firefly.update_transaction.assert_not_awaited()

    start = upload(
        client,
        "apply",
        statement,
        **apply_fields,
        account_choices=json.dumps({group: 5}),
    )
    assert start.status_code == 202
    assert completed_job(client, start.json()["id"])["applied"] == 2
    assert txs[1].destination_account.id == txs[3].destination_account.id == 5


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"description": "Invoice B-204"}, "descriptions differ"),
        ({"transaction_journal_id": None}, "journal ID"),
        ({"tags": ["do-usuniecia"]}, "tagged do-usuniecia"),
        ({"destination_account": None}, "current counterparty account"),
        (
            {"destination_account": replace(UNKNOWN_EXPENSE, type=AccountType.REVENUE)},
            "incompatible account type",
        ),
    ],
)
def test_number_match_explains_why_it_requires_review(
    counterparty_client, changes, reason
):
    client, firefly, txs = counterparty_client
    txs[1] = replace(txs[1], **changes)
    plan = upload(client, "preview", csv(OUT)).json()
    assert plan["ready"] == 0
    assert plan["rows"][0]["status"] == "review"
    assert reason in plan["rows"][0]["reason"]
    firefly.update_transaction.assert_not_awaited()


def test_same_amount_cards_are_grouped_by_merchant(card_client):
    client, firefly, txs = card_client
    firefly.fetch_accounts.return_value.append(
        SimplifiedAccount(5, "Other shop", "expense", active=True)
    )
    txs[3] = replace(txs[1], id=3, transaction_journal_id=103)
    txs[4] = replace(
        txs[1],
        id=4,
        transaction_journal_id=104,
        description="Transakcja kartą debetową, Other shop",
    )
    content = csv(CARD.replace("SHOP K.1", "OTHER SHOP"), CARD, CARD)
    fields = {"single_account_confirmed": "true"}
    preview = upload(client, "preview", content, **fields).json()
    assert (preview["ready"], preview["review"]) == (3, 0)
    assert [
        (row["proposed_account"], row["card_group_size"]) for row in preview["rows"]
    ] == [("Other shop", None), ("Shop K.1", 2), ("Shop K.1", 2)]

    result = upload(client, "apply", content, revision=preview["revision"], **fields)
    assert result.status_code == 202
    assert completed_job(client, result.json()["id"])["applied"] == 3
    assert [txs[key].destination_account.id for key in (1, 3, 4)] == [2, 2, 5]


def test_balanced_cards_with_an_existing_assignment_stay_in_review(card_client):
    client, firefly, txs = card_client
    txs[3] = replace(
        txs[1],
        id=3,
        transaction_journal_id=103,
        destination_account=SimplifiedAccountRef(
            5, "My shop alias", AccountType.EXPENSE
        ),
    )
    preview = upload(
        client, "preview", csv(CARD, CARD), single_account_confirmed="true"
    ).json()
    assert (preview["ready"], preview["review"]) == (0, 2)
    assert all(row["choice_group"] is None for row in preview["rows"])
    firefly.update_transaction.assert_not_awaited()


@pytest.mark.parametrize("selection", ["create", 2])
def test_explicit_choice_fills_missing_merchant_account_once(card_client, selection):
    client, firefly, txs = card_client
    accounts = firefly.fetch_accounts.return_value
    accounts[1] = replace(accounts[1], name="Shop")
    txs[3] = replace(txs[1], id=3, transaction_journal_id=103)

    async def create_account(*, name, account_type):
        account = SimplifiedAccount(5, name, account_type, active=True)
        accounts.append(account)
        return account

    firefly.create_account = AsyncMock(side_effect=create_account)
    content = csv(CARD, CARD)
    fields = {"single_account_confirmed": "true"}
    preview = upload(client, "preview", content, **fields).json()
    assert (preview["ready"], preview["review"]) == (0, 2)
    first, second = preview["rows"]
    group = first["choice_group"]
    assert group == second["choice_group"]
    assert first["new_account_name"] == "Shop K.1"
    assert first["card_group_size"] == 2
    assert [(a["id"], a["match"]) for a in first["candidates"]] == [(2, "generic")]

    fields["revision"] = preview["revision"]
    assert upload(client, "apply", content, **fields).status_code == 422
    firefly.create_account.assert_not_awaited()
    firefly.update_transaction.assert_not_awaited()
    result = upload(
        client,
        "apply",
        content,
        account_choices=json.dumps({group: selection}),
        **fields,
    )
    assert result.status_code == 202
    assert completed_job(client, result.json()["id"])["applied"] == 2
    if selection == "create":
        firefly.create_account.assert_awaited_once_with(
            name="Shop K.1", account_type="expense"
        )
        expected_id = 5
    else:
        firefly.create_account.assert_not_awaited()
        expected_id = 2
    assert txs[1].destination_account.id == txs[3].destination_account.id == expected_id


def test_uncertain_expense_creation_stops_and_fresh_preview_reuses_account(card_client):
    client, firefly, txs = card_client
    accounts = firefly.fetch_accounts.return_value
    accounts[1] = replace(accounts[1], name="Shop")
    txs[3] = replace(txs[1], id=3, transaction_journal_id=103)

    async def create_account(*, name, account_type):
        accounts.append(SimplifiedAccount(5, name, account_type, active=True))
        raise FireflyAPIError("Connection lost after creation.")

    firefly.create_account = AsyncMock(side_effect=create_account)
    content = csv(CARD, CARD)
    fields = {"single_account_confirmed": "true"}
    preview = upload(client, "preview", content, **fields).json()
    group = preview["rows"][0]["choice_group"]
    result = upload(
        client,
        "apply",
        content,
        account_choices=json.dumps({group: "create"}),
        revision=preview["revision"],
        **fields,
    )
    assert result.status_code == 202
    job = completed_job(client, result.json()["id"])
    assert (job["status"], job["applied"], job["failed"]) == ("failed", 0, 1)
    firefly.update_transaction.assert_not_awaited()

    refreshed = upload(client, "preview", content, **fields).json()
    assert refreshed["ready"] == 2
    result = upload(client, "apply", content, revision=refreshed["revision"], **fields)
    assert result.status_code == 202
    assert completed_job(client, result.json()["id"])["applied"] == 2
    firefly.create_account.assert_awaited_once()
    assert txs[1].destination_account.id == txs[3].destination_account.id == 5
    completed = upload(client, "preview", content, **fields).json()
    assert (completed["ready"], completed["review"], completed["skipped"]) == (0, 0, 2)
