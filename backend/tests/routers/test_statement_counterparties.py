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
        1: transaction(source_account=ASSET, destination_account=UNKNOWN_EXPENSE),
        2: transaction(
            2,
            amount=Decimal("12"),
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
        if patch.destination_id == 2:
            tx = replace(
                tx,
                destination_account=SimplifiedAccountRef(
                    2, "Shop", AccountType.EXPENSE
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
    txs[1] = replace(txs[1], reconciled=True)
    assert (
        upload(client, "apply", content, revision=plan["revision"]).status_code == 409
    )
    firefly.update_transaction.assert_not_awaited()


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
