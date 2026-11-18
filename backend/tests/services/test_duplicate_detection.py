from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations
from random import Random

import pytest

from services.domain.transaction import (
    AccountRef,
    AccountType,
    Currency,
    FXContext,
    Transaction,
    TxType,
)
from services.duplicates import account_direction, find_duplicate_page

BANK = AccountRef(1, "Bank", AccountType.ASSET)
OTHER_BANK = AccountRef(2, "Other bank", AccountType.ASSET)
SHOP = AccountRef(3, "Shop", AccountType.EXPENSE)
PLN = Currency("PLN", "zł", 2)
DAY = date(2026, 11, 10)


def tx(id, *, day=0, tags=None, **changes):
    return replace(
        Transaction(
            id=id,
            date=DAY + timedelta(days=day),
            amount=Decimal("12.34"),
            type=TxType.WITHDRAWAL,
            description=f"Payment {id}",
            tags={"bank"} if tags is None else tags,
            notes=None,
            category=None,
            currency=PLN,
            source_account=BANK,
            destination_account=SHOP,
        ),
        **changes,
    )


def find(transactions, **changes):
    args = {
        "account_id": 1,
        "date_from": DAY - timedelta(days=10),
        "date_to": DAY + timedelta(days=10),
        "tolerance_days": 2,
        "offset": 0,
        "limit": 100,
    }
    return find_duplicate_page(transactions, **(args | changes))


def ids(transactions, **changes):
    pairs, _ = find(transactions, **changes)
    return [(p.first.id, p.second.id) for p in pairs]


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ({"bank"}, {"csv"}, True),
        ({"bank", "common"}, {"csv", "common"}, True),
        (set(), {"csv"}, True),
        ({"csv"}, set(), True),
        (set(), set(), False),
        ({"a", "b"}, {"b", "a"}, False),
    ],
)
def test_tag_sets(left, right, expected):
    assert bool(ids([tx(1, tags=left), tx(2, tags=right)])) == expected


@pytest.mark.parametrize(
    "delta,tolerance,expected",
    [(0, 0, True), (1, 0, False), (2, 2, True), (-2, 2, True), (3, 2, False)],
)
def test_date_window(delta, tolerance, expected):
    assert (
        bool(ids([tx(1), tx(2, day=delta, tags={"csv"})], tolerance_days=tolerance))
        == expected
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": Decimal("12.35")},
        {"currency": Currency("EUR", "€", 2)},
        {"source_account": OTHER_BANK},
        {"source_account": None},
        {"type": TxType.DEPOSIT, "source_account": SHOP, "destination_account": BANK},
        {"destination_account": BANK},
    ],
)
def test_amount_currency_account_and_direction_isolation(changes):
    assert ids([tx(1), tx(2, tags={"csv"}, **changes)]) == []


def test_different_types_same_account_direction_match():
    assert ids(
        [
            tx(1),
            tx(2, tags={"csv"}, type=TxType.TRANSFER, destination_account=OTHER_BANK),
        ]
    ) == [(1, 2)]
    incoming = tx(1, type=TxType.DEPOSIT, source_account=SHOP, destination_account=BANK)
    transfer = tx(
        2,
        tags={"csv"},
        type=TxType.TRANSFER,
        source_account=OTHER_BANK,
        destination_account=BANK,
    )
    assert ids([incoming, transfer]) == [(1, 2)]
    assert account_direction(transfer, 1) == "incoming"


def test_incoming_fx_transfer_is_not_compared_using_source_currency_amount():
    transfer = tx(
        1,
        type=TxType.TRANSFER,
        source_account=OTHER_BANK,
        destination_account=BANK,
        fx=FXContext(Currency("EUR", "€", 2), Decimal("3")),
    )
    assert account_direction(transfer, 1) is None
    assert account_direction(transfer, 2) == "outgoing"


def test_exact_decimal_amounts_do_not_round_into_matches():
    assert ids([tx(1, amount=Decimal("12.340")), tx(2, tags={"csv"})]) == [(1, 2)]
    assert ids([tx(1, amount=Decimal("12.341")), tx(2, tags={"csv"})]) == []


def test_both_dates_must_be_in_inclusive_range():
    rows = [tx(1), tx(2, day=2, tags={"csv"})]
    assert ids(rows, date_from=DAY, date_to=DAY + timedelta(days=2)) == [(1, 2)]
    assert ids(rows, date_from=DAY + timedelta(days=1)) == []
    assert ids(rows, date_to=DAY + timedelta(days=1)) == []


def test_pairs_do_not_infer_transitive_matches_or_remove_repeated_payments():
    rows = [tx(1, tags={"a"}), tx(2, day=2, tags={"b"}), tx(3, day=4, tags={"c"})]
    assert set(ids(rows)) == {(1, 2), (2, 3)}
    assert ids([tx(1), tx(2, tags={"csv"}, description="Payment 1")]) == [(1, 2)]


def test_no_self_matches_duplicates_or_pagination_drift():
    rows = [tx(3, tags={"c"}), tx(1, tags={"a"}), tx(2, tags={"b"}), tx(1, tags={"a"})]
    assert ids(rows) == [(1, 2), (1, 3), (2, 3)]
    pages = [find(list(reversed(rows)), offset=n, limit=1) for n in range(3)]
    assert all(total == 3 for _, total in pages)
    assert [(pairs[0].first.id, pairs[0].second.id) for pairs, _ in pages] == ids(rows)
    assert find(rows, offset=3) == ([], 3)


def test_sliding_window_matches_independent_brute_force_reference():
    random = Random(130)
    rows = [
        tx(
            i,
            day=random.randrange(-10, 11),
            tags=set(random.sample(["a", "b", "c"], random.randrange(4))),
            source_account=random.choice([BANK, OTHER_BANK]),
            amount=Decimal(random.randrange(3)),
            currency=random.choice([PLN, Currency("EUR", "€", 2)]),
        )
        for i in range(1, 151)
    ]
    expected = {
        (a.id, b.id)
        for a, b in combinations(rows, 2)
        if a.source_account == b.source_account == BANK
        and a.amount == b.amount
        and a.currency.code == b.currency.code
        and a.tags != b.tags
        and abs((a.date - b.date).days) <= 2
    }
    actual, total = find(rows, limit=10000)
    assert {(p.first.id, p.second.id) for p in actual} == expected
    assert total == len(expected)


def test_luciferin_excludes_split_groups_before_duplicate_analysis():
    """Guard the dependency contract: never treat split legs as payments."""
    from ff_iii_luciferin.mappers.transaction_mapper import map_transaction
    from ff_iii_luciferin.openapi.openapi_client.models.transaction_read import (
        TransactionRead,
    )

    split = {
        "type": "withdrawal",
        "date": "2026-11-10T00:00:00Z",
        "amount": "12.34",
        "description": "Split leg",
        "source_id": "1",
        "destination_id": "3",
    }
    group = TransactionRead.model_validate(
        {
            "type": "transactions",
            "id": "999",
            "links": {},
            "attributes": {"transactions": [split, split]},
        }
    )
    mapped = map_transaction(group)
    assert mapped.tx is None
    assert mapped.reason == "multipart"
