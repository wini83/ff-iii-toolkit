"""Read-only duplicate candidates, indexed by account movement and tag set."""

from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from heapq import nsmallest
from typing import Literal

from services.domain.transaction import AccountType, Transaction, TxTag, TxType

Direction = Literal["incoming", "outgoing"]


@dataclass(frozen=True, slots=True)
class DuplicatePair:
    first: Transaction
    second: Transaction
    direction: Direction

    @property
    def key(self) -> tuple[int, int, int]:
        return (
            -max(self.first.date, self.second.date).toordinal(),
            self.first.id,
            self.second.id,
        )


def account_direction(tx: Transaction, account_id: int) -> Direction | None:
    source = tx.source_account
    destination = tx.destination_account
    outgoing = (
        source is not None
        and source.type == AccountType.ASSET
        and source.id == account_id
    )
    incoming = (
        destination is not None
        and destination.type == AccountType.ASSET
        and destination.id == account_id
    )
    if outgoing == incoming:
        return None
    # The simplified model cannot reliably express the destination-account
    # amount of a foreign-currency transfer. Do not compare its source amount.
    if incoming and tx.type == TxType.TRANSFER and tx.fx is not None:
        return None
    return "outgoing" if outgoing else "incoming"


def candidate_pairs(
    transactions: list[Transaction],
    *,
    account_id: int,
    date_from: date,
    date_to: date,
    tolerance_days: int,
) -> Iterator[DuplicatePair]:
    # Split groups are excluded by Luciferin before entering the snapshot.
    buckets: dict[tuple[str, Decimal, Direction], list[Transaction]] = defaultdict(list)
    seen: set[int] = set()
    for tx in transactions:
        if tx.id in seen or not date_from <= tx.date <= date_to:
            continue
        seen.add(tx.id)
        direction = account_direction(tx, account_id)
        if direction is not None:
            buckets[(tx.currency.code, abs(tx.amount), direction)].append(tx)

    for (_, _, direction), bucket in buckets.items():
        window: deque[tuple[Transaction, tuple[str, ...]]] = deque()
        active: dict[tuple[str, ...], dict[int, Transaction]] = {}
        for tx in sorted(bucket, key=lambda tx: (tx.date, tx.id)):
            while window and (tx.date - window[0][0].date).days > tolerance_days:
                expired, expired_tags = window.popleft()
                del active[expired_tags][expired.id]
                if not active[expired_tags]:
                    del active[expired_tags]
            # The cleanup marker must not create candidates by itself.
            tags = tuple(sorted(tx.tags - {TxTag.duplicate_delete.value}))
            # Only different tag sets within the date window are compared.
            # Identical sets (including two empty sets) never get paired.
            for other_tags, others in active.items():
                if other_tags != tags:
                    for other in others.values():
                        first, second = (tx, other) if tx.id < other.id else (other, tx)
                        yield DuplicatePair(first, second, direction)
            active.setdefault(tags, {})[tx.id] = tx
            window.append((tx, tags))


def find_duplicate_page(
    transactions: list[Transaction],
    *,
    account_id: int,
    date_from: date,
    date_to: date,
    tolerance_days: int,
    offset: int,
    limit: int,
) -> tuple[list[DuplicatePair], int]:
    total = 0

    def counted() -> Iterator[DuplicatePair]:
        nonlocal total
        for pair in candidate_pairs(
            transactions,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            tolerance_days=tolerance_days,
        ):
            total += 1
            yield pair

    # Retain only the requested prefix, even if there are many candidates.
    page = nsmallest(offset + limit, counted(), key=lambda pair: pair.key)
    return page[offset:], total
