"""Preview evidence grouped without assigning individual transactions by order."""

from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ff_iii_luciferin.domain.models import SimplifiedTx

from services.reconciliation.models import (
    BankLine,
    OperationEvidence,
    OperationGroup,
    OperationReason,
)

CARD_PREFIX = "transakcja kartą debetową"
GENERIC_BLIK = {
    "blik",
    "płatność blik",
    "płatność blik online",
    "płatność blik w sklepie",
    "płatność blik w terminalu",
    "płatność blik w internecie",
    "transakcja blik",
    "transakcja blik w internecie",
}


def description_key(value: str) -> str:
    return " ".join(value.lower().split())


def card_key(value: str) -> str:
    parts = value.split(",", 1)
    if len(parts) == 2 and parts[0].strip() == CARD_PREFIX:
        return parts[1].strip()
    return ""


@dataclass(frozen=True)
class Entry:
    evidence: OperationEvidence
    key: str
    card: str = ""

    @property
    def generic(self) -> bool:
        return self.key in GENERIC_BLIK or self.card in GENERIC_BLIK


def comparison_key(bank: Entry, firefly: Entry) -> str:
    if bank.generic or firefly.generic:
        return ""
    if bank.key == firefly.key:
        return bank.key
    if firefly.card and (
        bank.key == firefly.card or bank.key.startswith(firefly.card + " ")
    ):
        return firefly.card
    return ""


def connected_groups(
    bank: dict[int, Entry],
    firefly: dict[int, Entry],
    get_candidates: Callable[[Entry], Iterator[Entry]],
) -> Iterator[tuple[list[Entry], list[Entry], set[str]]]:
    bank_titles: dict[tuple[date, str], list[Entry]] = defaultdict(list)
    firefly_titles: dict[tuple[date, str], list[Entry]] = defaultdict(list)
    for entry in bank.values():
        bank_titles[entry.evidence.date, entry.key].append(entry)
    for entry in firefly.values():
        firefly_titles[entry.evidence.date, entry.key].append(entry)

    bank_clusters = {items[0].evidence.id: items for items in bank_titles.values()}
    firefly_clusters = {
        items[0].evidence.id: items for items in firefly_titles.values()
    }
    edges: dict[int, dict[int, str]] = {}
    reverse: dict[int, list[int]] = defaultdict(list)
    for bank_id, items in bank_clusters.items():
        entry = items[0]
        links = {}
        for tx in get_candidates(entry):
            tx_id = tx.evidence.id
            if tx_id not in firefly_clusters:
                continue
            key = comparison_key(entry, tx)
            if key:
                links[tx_id] = key
                reverse[tx_id].append(bank_id)
        if links:
            edges[bank_id] = links

    # Follow both sides of each component. A shared candidate belongs to one
    # component even when several descriptions or dates can explain it.
    seen_bank: set[int] = set()
    seen_firefly: set[int] = set()
    for bank_id in edges:
        if bank_id in seen_bank:
            continue
        todo = [bank_id]
        bank_ids = []
        firefly_ids = []
        keys: set[str] = set()
        seen_bank.add(bank_id)
        while todo:
            current = todo.pop()
            bank_ids.append(current)
            for tx_id, key in edges[current].items():
                keys.add(key)
                if tx_id in seen_firefly:
                    continue
                seen_firefly.add(tx_id)
                firefly_ids.append(tx_id)
                for other in reverse[tx_id]:
                    if other not in seen_bank:
                        seen_bank.add(other)
                        todo.append(other)
        yield (
            sorted(
                (entry for item in bank_ids for entry in bank_clusters[item]),
                key=lambda entry: entry.evidence.id,
            ),
            sorted(
                (entry for item in firefly_ids for entry in firefly_clusters[item]),
                key=lambda entry: entry.evidence.id,
            ),
            keys,
        )


def group_operations(
    bank: Sequence[BankLine],
    live: dict[int, tuple[SimplifiedTx, Decimal]],
    tolerance_days: int,
) -> list[OperationGroup]:
    buckets: dict[tuple[str, Decimal], tuple[dict[int, Entry], dict[int, Entry]]] = (
        defaultdict(lambda: ({}, {}))
    )
    for row in bank:
        buckets[row.currency, Decimal(row.amount)][0][row.row_id] = Entry(
            OperationEvidence(
                id=row.row_id, date=row.date, description=row.description
            ),
            description_key(row.description),
        )
    for tx, movement in live.values():
        key = description_key(tx.description)
        buckets[tx.currency.code, movement][1][tx.id] = Entry(
            OperationEvidence(id=tx.id, date=tx.date, description=tx.description),
            key,
            card_key(key),
        )

    groups = []
    for (currency, amount), (bank_entries, firefly_entries) in buckets.items():
        groups.extend(
            compare_bucket(
                currency, amount, bank_entries, firefly_entries, tolerance_days
            )
        )
    return groups


def compare_bucket(
    currency: str,
    amount: Decimal,
    bank: dict[int, Entry],
    firefly: dict[int, Entry],
    tolerance_days: int,
) -> list[OperationGroup]:
    groups: list[OperationGroup] = []

    def record(
        left: list[Entry],
        right: list[Entry],
        reason: OperationReason,
        key: str | None = None,
    ) -> None:
        countable = reason in {"exact", "card_prefix", "bank_only", "firefly_only"}
        difference = len(left) - len(right)
        groups.append(
            OperationGroup(
                amount=str(amount),
                currency=currency,
                bank=[item.evidence for item in left],
                firefly=[item.evidence for item in right],
                reason=reason,
                compared_key=key,
                missing_count=max(difference, 0) if countable else 0,
                excess_count=max(-difference, 0) if countable else 0,
            )
        )
        for entry in left:
            del bank[entry.evidence.id]
        for entry in right:
            del firefly[entry.evidence.id]

    exact_bank: dict[tuple[date, str], list[Entry]] = defaultdict(list)
    exact_firefly: dict[tuple[date, str], list[Entry]] = defaultdict(list)
    card_titles: set[tuple[date, str]] = set()
    for entry in bank.values():
        if not entry.generic:
            exact_bank[entry.evidence.date, entry.key].append(entry)
    for entry in firefly.values():
        if not entry.generic:
            exact_firefly[entry.evidence.date, entry.key].append(entry)
            if entry.card:
                card_titles.add((entry.evidence.date, entry.card))
    # Reserve complete exact groups first. An imbalanced exact group can still
    # have other plausible candidates. A card entry with the very same merchant
    # key could also be an extra occurrence of an otherwise balanced exact group.
    for key, left in exact_bank.items():
        right = exact_firefly.get(key, [])
        if len(left) == len(right) and key not in card_titles:
            record(left, right, "exact", key[1])

    def by_day() -> dict[int, list[Entry]]:
        index: dict[int, list[Entry]] = defaultdict(list)
        for tx in firefly.values():
            index[tx.evidence.date.toordinal()].append(tx)
        return index

    daily = by_day()
    same_day = list(
        connected_groups(
            bank, firefly, lambda entry: iter(daily[entry.evidence.date.toordinal()])
        )
    )
    for left, right, keys in same_day:
        # A common prefix may refer to separate points of sale. Without stronger
        # evidence, distinct bank titles in one component cannot be counted as one payment.
        if len(keys) != 1 or len({entry.key for entry in left}) != 1:
            record(left, right, "overlap")
        elif len(left) == len(right):
            key = next(iter(keys))
            exact = all(entry.key == key for entry in right)
            record(left, right, "exact" if exact else "card_prefix", key)

    daily = by_day()

    def nearby(entry: Entry) -> Iterator[Entry]:
        for offset in range(-tolerance_days, tolerance_days + 1):
            yield from daily[entry.evidence.date.toordinal() + offset]

    for left, right, keys in list(connected_groups(bank, firefly, nearby)):
        key = next(iter(keys)) if len(keys) == 1 else None
        if len({entry.evidence.date for entry in left + right}) > 1:
            record(left, right, "date_shift", key)
        else:
            exact = all(entry.key == key for entry in right)
            record(left, right, "exact" if exact else "card_prefix", key)

    remaining_bank: dict[int, list[Entry]] = defaultdict(list)
    for entry in bank.values():
        remaining_bank[entry.evidence.date.toordinal()].append(entry)
    remaining_firefly = by_day()
    for day in sorted(remaining_bank.keys() | remaining_firefly.keys()):
        left, right = remaining_bank[day], remaining_firefly[day]
        if any(entry.generic for entry in left + right):
            record(left, right, "generic_blik")
        elif not left:
            record(left, right, "firefly_only")
        elif not right:
            record(left, right, "bank_only")
        else:
            record(left, right, "different_descriptions")
    return groups
