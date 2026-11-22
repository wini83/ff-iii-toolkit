import asyncio
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from ff_iii_luciferin.api import FireflyAPIError
from ff_iii_luciferin.domain.models import (
    AccountType,
    Currency,
    SimplifiedAccountRef,
    SimplifiedTx,
    TxType,
)

from services.reconciliation.matching import compare_statement
from services.reconciliation.parser import parse_statement
from services.reconciliation.service import ComparisonChanged, ReconciliationService

DAY = date(2026, 11, 16)
# Deliberately invalid, synthetic account number; never use bank-export data here.
ASSET = SimplifiedAccountRef(1, "Bank", AccountType.ASSET, "PL" + "0" * 26)
SHOP = SimplifiedAccountRef(8, "Shop", AccountType.EXPENSE)
HEADER = (
    "Data transakcji;Kwota w walucie rachunku;Waluta rachunku;Szczegóły transakcji\n"
)


def statement(*rows):
    return (
        "Metadata\n" + HEADER + "\n".join(rows or ["16-11-2026;-30,04;PLN;faktura 123"])
    ).encode()


def transaction(id=1, **changes):
    return replace(
        SimplifiedTx(
            date=DAY,
            amount=Decimal("30.04"),
            id=id,
            description="faktura 123",
            tags=[],
            notes="keep notes",
            category=None,
            currency=Currency("PLN", "zł", 2),
            fx=None,
            type=TxType.WITHDRAWAL,
            source_account=ASSET,
            destination_account=SHOP,
            reconciled=False,
            transaction_journal_id=id + 100,
        ),
        **changes,
    )


def compare(content=None, txs=None, **changes):
    return compare_statement(
        parse_statement(content or statement()),
        txs if txs is not None else [transaction()],
        account_id=1,
        date_from=DAY - timedelta(days=2),
        date_to=DAY + timedelta(days=2),
        tolerance_days=changes.get("tolerance", 0),
        file_hash="abc",
    )


def test_unique_matching_uses_signed_decimal_currency_date_and_normalized_title():
    preview = compare(statement("16-11-2026;-30,04;PLN;  FAKTURA   123  "))
    assert preview.rows[0].status == "matched"
    assert preview.rows[0].candidates[0].can_mark
    assert preview.totals[0].difference == "0.00"
    assert (
        compare(txs=[transaction(amount=Decimal("30.05"))]).rows[0].status
        == "bank_only"
    )
    assert (
        compare(txs=[transaction(currency=Currency("EUR", "€", 2))]).rows[0].status
        == "bank_only"
    )
    assert (
        compare(
            txs=[
                transaction(
                    type=TxType.DEPOSIT, source_account=SHOP, destination_account=ASSET
                )
            ]
        )
        .rows[0]
        .status
        == "bank_only"
    )
    assert (
        compare(txs=[transaction(date=DAY + timedelta(days=1))]).rows[0].status
        == "bank_only"
    )
    assert (
        compare(txs=[transaction(date=DAY + timedelta(days=1))], tolerance=1)
        .rows[0]
        .status
        == "matched"
    )
    assert (
        compare(txs=[transaction(description="other title")]).rows[0].status == "review"
    )


def test_repeated_bank_rows_or_firefly_rows_are_ambiguous_and_never_consumed_twice():
    preview = compare(
        statement(
            "16-11-2026;-30,04;PLN;faktura 123", "16-11-2026;-30,04;PLN;faktura 123"
        )
    )
    assert [row.status for row in preview.rows] == ["review", "review"]
    assert compare(txs=[transaction(), transaction(2)]).rows[0].status == "review"
    many = compare(txs=[transaction(i) for i in range(1, 9)]).rows[0]
    assert many.candidate_count == 8 and len(many.candidates) == 5


def test_differences_and_read_only_restrictions():
    preview = compare(txs=[transaction(), transaction(2, amount=Decimal("10"))])
    assert [tx.id for tx in preview.firefly_only] == [2]
    assert Decimal(preview.totals[0].difference) == Decimal("10")
    for changed in [
        {"type": TxType.TRANSFER},
        {"tags": ["do-usuniecia"]},
        {"reconciled": None},
        {"transaction_journal_id": None},
    ]:
        assert not compare(txs=[transaction(**changed)]).rows[0].candidates[0].can_mark


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "0", "no amount"])
def test_invalid_amount_is_rejected(amount):
    with pytest.raises(ValueError):
        parse_statement(statement(f"16-11-2026;{amount};PLN;title"))


def test_parser_handles_bom_header_first_and_optional_booking_date():
    data = (
        "\ufeff"
        + HEADER.strip()
        + ";Data księgowania\n15-11-2026;-30,04;PLN;title;16-11-2026\n"
    ).encode()
    assert parse_statement(data)[0].date == DAY
    with pytest.raises(ValueError):
        parse_statement(b"bad headers\n")
    with pytest.raises(ValueError):
        parse_statement(statement() + b"wrong;column;count\n")


def test_wrong_statement_account_is_rejected():
    content = (
        HEADER.strip() + ";Numer rachunku nadawcy\n16-11-2026;-30,04;PLN;title;OTHER\n"
    ).encode()
    with pytest.raises(ValueError, match="account does not match"):
        compare(content)


@pytest.fixture
def service(monkeypatch):
    client = MagicMock()
    client.get_transaction = AsyncMock(return_value=transaction())
    client.update_transaction = AsyncMock(return_value=transaction(reconciled=True))
    txs = [transaction()]

    async def fetch(*args, **kwargs):
        return list(txs), SimpleNamespace(multipart=0, invalid=0)

    monkeypatch.setattr(
        "services.reconciliation.service.fetch_transactions_with_stats", fetch
    )
    return ReconciliationService(client), client, txs


def test_apply_only_sets_reconciled_and_verifies_persisted_flag(service):
    svc, client, _ = service

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        client.get_transaction.side_effect = [
            transaction(),
            transaction(),
            transaction(reconciled=True),
        ]
        result = await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, [1])
        assert result.outcomes[0].status == "reconciled"

    asyncio.run(run())
    args, kwargs = client.update_transaction.await_args
    assert args[0] == 1
    assert args[1].reconciled is True
    assert (
        args[1].tags is None and args[1].notes is None and args[1].description is None
    )
    assert kwargs == {"apply_rules": False, "fire_webhooks": False}


def test_revision_changes_abort_before_writes(service):
    svc, client, txs = service

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        txs[0] = transaction(amount=Decimal("99"))
        with pytest.raises(ComparisonChanged):
            await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, [1])

    asyncio.run(run())
    client.update_transaction.assert_not_awaited()


@pytest.mark.parametrize("ids", [[99], [1, 1], []])
def test_invalid_selections_never_write(service, ids):
    svc, client, _ = service

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        with pytest.raises(ValueError):
            await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, ids)

    asyncio.run(run())
    client.update_transaction.assert_not_awaited()


def test_already_reconciled_is_idempotent(service):
    svc, client, txs = service

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        txs[0] = transaction(reconciled=True)
        client.get_transaction.return_value = txs[0]
        result = await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, [1])
        assert result.outcomes[0].status == "already_reconciled"

    asyncio.run(run())
    client.update_transaction.assert_not_awaited()


def test_write_error_can_be_verified_as_success(service):
    svc, client, _ = service
    client.update_transaction.side_effect = FireflyAPIError("Response lost")
    client.get_transaction.side_effect = [
        transaction(),
        transaction(),
        transaction(reconciled=True),
    ]

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        return await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, [1])

    assert asyncio.run(run()).outcomes[0].status == "reconciled"


def test_unknown_write_stops_remaining_batch(service):
    svc, client, txs = service
    txs.append(transaction(2, description="second"))
    content = statement(
        "16-11-2026;-30,04;PLN;faktura 123", "16-11-2026;-30,04;PLN;second"
    )
    client.update_transaction.side_effect = FireflyAPIError("Response lost")
    client.get_transaction.side_effect = [
        transaction(),
        txs[1],
        transaction(),
        FireflyAPIError("Read unavailable"),
    ]

    async def run():
        preview, _ = await svc.compare(content, 1, DAY, DAY, 0)
        return await svc.apply(content, 1, DAY, DAY, 0, preview.revision, [1, 2])

    result = asyncio.run(run())
    assert [item.status for item in result.outcomes] == ["unknown", "not_processed"]
    assert client.update_transaction.await_count == 1


def test_preflight_changed_transaction_aborts_entire_batch(service):
    svc, client, _ = service
    client.get_transaction.return_value = transaction(description="changed")

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        with pytest.raises(ComparisonChanged):
            await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, [1])

    asyncio.run(run())
    client.update_transaction.assert_not_awaited()


def test_changed_transaction_immediately_before_write_is_not_processed(service):
    svc, client, _ = service
    client.get_transaction.side_effect = [
        transaction(),
        transaction(tags=["do-usuniecia"]),
    ]

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        return await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, [1])

    assert asyncio.run(run()).outcomes[0].status == "not_processed"
    client.update_transaction.assert_not_awaited()


def test_transfer_cannot_be_selected_for_marking(service):
    svc, client, txs = service
    txs[0] = transaction(type=TxType.TRANSFER)

    async def run():
        preview, _ = await svc.compare(statement(), 1, DAY, DAY, 0)
        with pytest.raises(ValueError):
            await svc.apply(statement(), 1, DAY, DAY, 0, preview.revision, [1])

    asyncio.run(run())
    client.update_transaction.assert_not_awaited()
