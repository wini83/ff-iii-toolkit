import asyncio
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from ff_iii_luciferin.api import FireflyAPIError, FireflyClient
from ff_iii_luciferin.domain.models import (
    AccountType,
    Currency,
    SimplifiedAccountRef,
    SimplifiedTx,
    TxType,
)

from services.reconciliation.matching import compare_statement
from services.reconciliation.parser import parse_statement, statement_period
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
    assert [row.review_reason for row in preview.rows] == ["ambiguous", "ambiguous"]
    assert compare(txs=[transaction(), transaction(2)]).rows[0].status == "review"
    many = compare(txs=[transaction(i) for i in range(1, 9)]).rows[0]
    assert many.candidate_count == 8 and len(many.candidates) == 5


def test_review_explains_mismatched_title_and_shared_candidates():
    transactions = [transaction(description="Firefly title")]
    single = compare(txs=transactions).rows[0]
    assert single.review_reason == "title_mismatch"
    assert single.candidates[0].title_matches is False

    shared = compare(
        statement("16-11-2026;-30,04;PLN;Bank A", "16-11-2026;-30,04;PLN;Bank B"),
        txs=transactions,
    )
    assert [row.review_reason for row in shared.rows] == ["ambiguous", "ambiguous"]


@pytest.mark.parametrize(
    "bank_suffix, firefly_suffix, key_suffix",
    [
        ("", "", ""),
        (" Poznan PL", "", ""),
        (" GDAŃSK PL", "", ""),
        (" Poznan PL", " POZNAN PL", " poznan pl"),
    ],
)
def test_card_description_suggestion_preserves_originals_and_merchant_punctuation(
    bank_suffix,
    firefly_suffix,
    key_suffix,
):
    bank_title = f"  FIKCYJNY,   MARKET Z8287 K.2{bank_suffix}  "
    firefly_title = (
        f"Transakcja kartą debetową, Fikcyjny, Market Z8287 K.2{firefly_suffix}"
    )
    row = compare(
        statement(f"16-11-2026;-30,04;PLN;{bank_title}"),
        txs=[transaction(description=firefly_title)],
    ).rows[0]
    assert row.status == "review"
    assert row.review_reason == "description_normalization"
    assert row.bank.description == bank_title
    candidate = row.candidates[0]
    assert candidate.description == firefly_title
    assert (
        candidate.normalized_description_key
        == f"fikcyjny, market z8287 k.2{key_suffix}"
    )
    assert not candidate.can_mark


@pytest.mark.parametrize(
    "firefly_title",
    [
        "Transakcja kartą debetową, Fikcyjny, Market Z8288 K.2",
        "Transakcja kartą debetową, Fikcyjny, Market Z8287 K.3",
        "Transakcja kartą debetową, Fikcyjny Market Z8287 K.2",
        "Inna transakcja, Fikcyjny, Market Z8287 K.2 POZNAN PL",
        "Płatność BLIK w internecie",
    ],
)
def test_unexplained_descriptions_remain_manual_review_candidates(firefly_title):
    row = compare(
        statement("16-11-2026;-30,04;PLN;Fikcyjny, Market Z8287 K.2"),
        txs=[transaction(description=firefly_title)],
    ).rows[0]
    assert row.status == "review"
    assert row.review_reason == "title_mismatch"
    assert row.candidates[0].normalized_description_key is None


@pytest.mark.parametrize("bank_count, firefly_count", [(1, 2), (2, 1), (2, 2)])
def test_description_collisions_remain_ambiguous(bank_count, firefly_count):
    preview = compare(
        statement(*["16-11-2026;-30,04;PLN;Sklep Z8287 K.2 POZNAN PL"] * bank_count),
        txs=[
            transaction(i, description="Transakcja kartą debetową, Sklep Z8287 K.2")
            for i in range(1, firefly_count + 1)
        ],
    )
    assert [row.review_reason for row in preview.rows] == ["ambiguous"] * bank_count
    assert all(
        tx.normalized_description_key is None
        for row in preview.rows
        for tx in row.candidates
    )


@pytest.mark.parametrize(
    "bank_count, firefly_count, missing, excess",
    [(2, 2, 0, 0), (2, 1, 1, 0), (1, 2, 0, 1)],
)
def test_payment_groups_compare_quantities_without_pairing_rows(
    bank_count, firefly_count, missing, excess
):
    preview = compare(
        statement(*["16-11-2026;-6,00;PLN;Fikcyjny Market K.2 GDAŃSK PL"] * bank_count),
        txs=[
            transaction(
                i,
                description="Transakcja kartą debetową, Fikcyjny Market K.2",
                amount=Decimal("6"),
            )
            for i in range(1, firefly_count + 1)
        ],
    )
    [group] = preview.operation_groups
    assert group.reason == "card_prefix"
    assert Decimal(group.amount) == Decimal("-6")
    assert (len(group.bank), len(group.firefly)) == (bank_count, firefly_count)
    assert (group.missing_count, group.excess_count) == (missing, excess)
    assert all(row.status == "review" for row in preview.rows)


def test_additional_card_entry_with_the_exact_merchant_key_is_a_possible_duplicate():
    title = "Fikcyjny sklep K.2"
    preview = compare(
        statement(f"16-11-2026;-30,04;PLN;{title}"),
        txs=[
            transaction(description=title),
            transaction(2, description=f"Transakcja kartą debetową, {title}"),
        ],
    )
    [group] = preview.operation_groups
    assert group.reason == "card_prefix"
    assert group.excess_count == 1
    assert [entry.id for entry in group.firefly] == [1, 2]
    assert preview.rows[0].status == "matched"
    assert preview.rows[0].candidates[0].id == 1


@pytest.mark.parametrize(
    "csv_title, ff_title, expected_reason",
    [
        (
            "Fikcyjny Market K.20 GDAŃSK PL",
            "Transakcja kartą debetową, Fikcyjny Market K.2",
            "different_descriptions",
        ),
        ("Fikcyjny Market A", "Fikcyjny Market B", "different_descriptions"),
        ("Płatność BLIK w internecie", "Płatność BLIK w internecie", "generic_blik"),
    ],
)
def test_equal_day_currency_and_amount_do_not_confirm_generic_or_different_titles(
    csv_title, ff_title, expected_reason
):
    [group] = compare(
        statement(f"16-11-2026;-30,04;PLN;{csv_title}"),
        txs=[transaction(description=ff_title)],
    ).operation_groups
    assert group.reason == expected_reason
    assert len(group.bank) == len(group.firefly) == 1
    assert (group.missing_count, group.excess_count) == (0, 0)


def test_overlapping_points_are_reviewed_together_and_each_entry_is_counted_once():
    preview = compare(
        statement(
            "16-11-2026;-30,04;PLN;Fikcyjny Market K.2 GDAŃSK PL",
            "16-11-2026;-30,04;PLN;Fikcyjny Market K.20 GDAŃSK PL",
        ),
        txs=[
            transaction(description="Transakcja kartą debetową, Fikcyjny Market"),
            transaction(
                2, description="Transakcja kartą debetową, Fikcyjny Market K.2"
            ),
        ],
    )
    [group] = preview.operation_groups
    assert group.reason == "overlap"
    assert [entry.id for entry in group.bank] == [1, 2]
    assert [entry.id for entry in group.firefly] == [1, 2]
    assert (group.missing_count, group.excess_count) == (0, 0)


def test_same_day_exact_group_takes_precedence_and_shift_is_separate():
    preview = compare(
        statement(
            "16-11-2026;-30,04;PLN;Fikcyjny Market K.2 GDAŃSK PL",
            "17-11-2026;-30,04;PLN;Drugi fikcyjny sklep GDAŃSK PL",
        ),
        txs=[
            transaction(description="Fikcyjny Market K.2 GDAŃSK PL"),
            transaction(
                2, description="Transakcja kartą debetową, Drugi fikcyjny sklep"
            ),
        ],
        tolerance=1,
    )
    exact, shifted = preview.operation_groups
    assert exact.reason == "exact"
    assert shifted.reason == "date_shift"
    assert shifted.bank[0].date != shifted.firefly[0].date
    assert (shifted.missing_count, shifted.excess_count) == (0, 0)


def test_an_imbalanced_daily_group_can_be_explained_by_a_shift_without_assigning_pairs():
    title = "Fikcyjny sklep K.2"
    [group] = compare(
        statement(*[f"16-11-2026;-30,04;PLN;{title} GDAŃSK PL"] * 2),
        txs=[
            transaction(description=f"Transakcja kartą debetową, {title}"),
            transaction(
                2,
                date=DAY + timedelta(days=1),
                description=f"Transakcja kartą debetową, {title}",
            ),
        ],
        tolerance=1,
    ).operation_groups
    assert group.reason == "date_shift"
    assert len(group.bank) == len(group.firefly) == 2
    assert (group.missing_count, group.excess_count) == (0, 0)


def test_description_suggestions_require_an_exact_day_and_respect_verified_matches():
    title = "Transakcja kartą debetową, Sklep Z8287 K.2 POZNAŃ PL"
    csv_row = "16-11-2026;-30,04;PLN;Sklep Z8287 K.2"
    shifted = compare(
        statement(csv_row),
        txs=[transaction(description=title, date=DAY + timedelta(days=1))],
        tolerance=1,
    )
    assert shifted.rows[0].review_reason == "title_mismatch"
    reserved = compare(
        statement(csv_row, f"16-11-2026;-30,04;PLN;{title}"),
        txs=[transaction(description=title)],
    )
    assert [row.status for row in reserved.rows] == ["review", "matched"]
    assert reserved.rows[0].candidates[0].normalized_description_key is None
    assert reserved.rows[1].candidates[0].can_mark


def test_matching_titles_appear_first_in_truncated_ambiguous_candidates():
    transactions = [transaction(i, description="Other") for i in range(1, 7)]
    row = compare(txs=[*transactions, transaction(7), transaction(8)]).rows[0]
    assert row.review_reason == "ambiguous"
    assert row.candidate_count == 8
    assert [(tx.id, tx.title_matches) for tx in row.candidates[:2]] == [
        (7, True),
        (8, True),
    ]


def test_totals_preserve_decimal_precision_without_expanding_exponents():
    preview = compare(
        statement("16-11-2026;-0,00000001;PLN;faktura 123"),
        txs=[transaction(amount=Decimal("0.000000010000"))],
    )
    total = preview.totals[0]
    assert total.bank_total == "-1E-8"
    assert Decimal(total.firefly_total) == Decimal("-0.00000001")
    assert total.difference == "0E-12"


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


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "no amount"])
def test_invalid_amount_is_rejected(amount):
    with pytest.raises(ValueError):
        parse_statement(statement(f"16-11-2026;{amount};PLN;title"))


@pytest.mark.parametrize("amount", ["0,00", "-0,00"])
def test_zero_amount_preserves_the_statement_period_but_does_not_change_group_counts(
    amount,
):
    zero = f"15-11-2026;{amount};PLN;Płatność BLIK w internecie"
    content = statement(zero, "16-11-2026;-30,04;PLN;faktura 123")
    preview = compare(content)

    assert statement_period(content).date_from == DAY - timedelta(days=1)
    assert preview.skipped_zero_amount == 1
    [match] = preview.rows
    assert match.status == "matched"
    [group] = preview.reconciliation_groups
    assert len(group.bank) == 1 and group.eligible_ids == [1]


def test_statement_with_only_zero_amounts_has_no_selectable_groups():
    preview = compare(statement("16-11-2026;0,00;PLN;Fikcyjny wpis"), txs=[])
    assert preview.skipped_zero_amount == 1
    assert preview.rows == preview.reconciliation_groups == []


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


def test_parser_accepts_alior_booking_dates_with_empty_card_counterparties():
    content = (
        "Wyciąg za okres: 01-06-2026 – 30-06-2026\n"
        "Data transakcji;Data księgowania;Nazwa nadawcy;Nazwa odbiorcy;"
        "Szczegóły transakcji;Kwota operacji;Waluta operacji;"
        "Kwota w walucie rachunku;Waluta rachunku;Numer rachunku nadawcy;"
        "Numer rachunku odbiorcy\n"
        "10-06-2026;12-06-2026;;;Fikcyjna stacja 100 Miasto PL;-20,11;PLN;-20,11;PLN;;\n"
        "10-06-2026;12-06-2026;;;Karta**0000* Miasto IE;-50,00;PLN;-50,00;PLN;;\n"
        "10-06-2026;10-06-2026;Nadawca Testowy;Odbiorca Testowy;"
        "Przelew na telefon BLIK;-60,00;PLN;-60,00;PLN;0000;1111\n"
    ).encode()
    rows = parse_statement(content)
    assert [row.date.isoformat() for row in rows] == [
        "2026-06-12",
        "2026-06-12",
        "2026-06-10",
    ]
    assert [row.amount for row in rows] == ["-20.11", "-50.00", "-60.00"]
    assert [row.own_account for row in rows] == ["", "", "0000"]


def test_parser_reports_actual_file_line_and_field_after_multiline_records():
    content = (
        "Metadata\n"
        + HEADER
        + '16-11-2026;-30,04;PLN;"Fikcyjny\nopis"\n'
        + "\n16-11-2026;not-an-amount;PLN;Inny wpis\n"
    ).encode()
    with pytest.raises(ValueError) as error:
        parse_statement(content)
    assert str(error.value) == (
        'Invalid value in "Kwota w walucie rachunku" at CSV file line 6 (record 2)'
    )


def test_parser_reports_column_count_and_line_for_malformed_row():
    content = statement() + b"\n16-11-2026;PLN;missing column\n"
    with pytest.raises(ValueError) as error:
        parse_statement(content)
    assert str(error.value) == (
        "Invalid columns at CSV file line 4 (record 2): expected 4, found 3"
    )


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


def test_compare_fetches_all_types_through_real_luciferin_transport(monkeypatch):
    # Exercise the real helper, HTTP query, validation and mapper. Mocking the
    # fetch helper itself would hide its default withdrawal-only filter.
    requests = []
    other_asset = replace(ASSET, id=2, name="Savings")
    revenue = replace(SHOP, id=9, type=AccountType.REVENUE)
    transactions = [
        transaction(description="Shop"),
        transaction(
            2,
            type=TxType.DEPOSIT,
            description="Salary",
            amount=Decimal("100"),
            source_account=revenue,
            destination_account=ASSET,
        ),
        transaction(
            3,
            type=TxType.TRANSFER,
            description="Savings",
            amount=Decimal("20"),
            destination_account=other_asset,
        ),
        transaction(
            4,
            type=TxType.DEPOSIT,
            description="Refund",
            amount=Decimal("5"),
            source_account=revenue,
            destination_account=ASSET,
        ),
    ]
    account_types = {
        AccountType.ASSET: "Asset account",
        AccountType.EXPENSE: "Expense account",
        AccountType.REVENUE: "Revenue account",
    }

    def dto(tx):
        split = {
            "type": tx.type.value,
            "date": f"{DAY}T00:00:00Z",
            "transaction_journal_id": str(tx.transaction_journal_id),
            "amount": str(tx.amount),
            "description": tx.description,
            "currency_code": "PLN",
            "currency_symbol": "zl",
            "currency_decimal_places": 2,
            "reconciled": False,
        }
        for side in ("source", "destination"):
            account = getattr(tx, f"{side}_account")
            split.update(
                {
                    f"{side}_id": str(account.id),
                    f"{side}_name": account.name,
                    f"{side}_type": account_types[account.type],
                    f"{side}_iban": account.iban,
                }
            )
        return {
            "type": "transactions",
            "id": str(tx.id),
            "attributes": {"transactions": [split]},
            "links": {},
        }

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/transactions"
        assert request.url.params["type"] == "all"
        assert request.url.params["start"] == DAY.isoformat()
        assert request.url.params["end"] == DAY.isoformat()
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "data": [dto(tx) for tx in transactions[(page - 1) * 2 : page * 2]],
                "meta": {},
                "links": {
                    "next": "https://firefly.example/api/v1/transactions?page=2"
                    if page == 1
                    else None
                },
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(handler)
    )

    async def run():
        client = FireflyClient("https://firefly.example", "synthetic-token")
        try:
            preview, _ = await ReconciliationService(client).compare(
                statement(
                    "16-11-2026;-30,04;PLN;Shop",
                    "16-11-2026;100,00;PLN;Salary",
                    "16-11-2026;-20,00;PLN;Savings",
                ),
                1,
                DAY,
                DAY,
                0,
            )
            assert [row.status for row in preview.rows] == ["matched"] * 3
            assert [row.candidates[0].type for row in preview.rows] == [
                "withdrawal",
                "deposit",
                "transfer",
            ]
            assert [row.candidates[0].can_mark for row in preview.rows] == [
                True,
                True,
                False,
            ]
            assert [tx.id for tx in preview.firefly_only] == [4]
            assert Decimal(preview.totals[0].bank_total) == Decimal("49.96")
            assert Decimal(preview.totals[0].firefly_total) == Decimal("54.96")
            assert Decimal(preview.totals[0].difference) == Decimal("-5")
            assert len(requests) == 2
        finally:
            await client.close()

    asyncio.run(run())
