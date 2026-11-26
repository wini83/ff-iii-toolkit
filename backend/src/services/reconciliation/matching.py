"""Conservative one-to-one matches; amount-only similarities are review items."""

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from ff_iii_luciferin.domain.models import SimplifiedTx

from services.reconciliation.models import (
    BankLine,
    ComparisonRow,
    CurrencyDifference,
    FireflyLine,
    OperationEvidence,
    ReconciliationGroup,
    ReconciliationPreview,
)
from services.reconciliation.operation_groups import group_operations


def normalize_title(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def account_movement(tx: SimplifiedTx, account_id: int) -> Decimal | None:
    source, destination = tx.source_account, tx.destination_account
    outgoing = (
        source is not None and source.type.value == "asset" and source.id == account_id
    )
    incoming = (
        destination is not None
        and destination.type.value == "asset"
        and destination.id == account_id
    )
    if outgoing == incoming or (
        incoming and tx.type.value == "transfer" and tx.fx is not None
    ):
        return None
    amount = tx.amount.copy_abs()
    return amount.copy_negate() if outgoing else amount


def fingerprint(tx: SimplifiedTx) -> str:
    """Ignore reconciliation state so a retry can safely skip completed writes."""
    data = {
        "id": tx.id,
        "journal": tx.transaction_journal_id,
        "date": tx.date.isoformat(),
        "amount": amount_key(tx.amount),
        "currency": tx.currency.code,
        "description": tx.description,
        "type": tx.type.value,
        "source": (
            tx.source_account.id,
            tx.source_account.type.value,
            tx.source_account.iban,
        )
        if tx.source_account
        else None,
        "destination": (
            tx.destination_account.id,
            tx.destination_account.type.value,
            tx.destination_account.iban,
        )
        if tx.destination_account
        else None,
        "tags": sorted(tx.tags),
        "fx": repr(tx.fx),
        "external_id": tx.external_id,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def line_for(
    tx: SimplifiedTx,
    movement: Decimal,
    *,
    title_matches: bool | None = None,
    normalized_description_key: str | None = None,
) -> FireflyLine:
    restriction = None
    if tx.type.value == "transfer":
        restriction = "Transfer: Firefly reconciles both account legs; review manually"
    elif "do-usuniecia" in tx.tags:
        restriction = "Marked for deletion"
    elif tx.reconciled is None or tx.transaction_journal_id is None:
        restriction = "Missing reconciliation state or journal ID"
    return FireflyLine(
        id=tx.id,
        date=tx.date,
        amount=str(movement),
        currency=tx.currency.code,
        description=tx.description,
        type=tx.type.value,
        reconciled=tx.reconciled,
        can_mark=restriction is None and normalized_description_key is None,
        restriction=restriction,
        title_matches=title_matches,
        normalized_description_key=normalized_description_key,
    )


def account_number(value: str) -> str:
    return re.sub(r"\s+", "", value).upper().removeprefix("PL")


def amount_key(amount: Decimal) -> str:
    """Canonical decimal identity without arithmetic or context rounding."""
    digits = "".join(map(str, amount.as_tuple().digits)).rstrip("0")
    if not digits:
        return "0"
    sign = "-" if amount.is_signed() else ""
    return f"{sign}{digits}e{amount.adjusted() + 1 - len(digits)}"


def reconciliation_groups(
    bank: Sequence[BankLine],
    live: dict[int, tuple[SimplifiedTx, Decimal]],
    account_id: int,
) -> list[ReconciliationGroup]:
    bank_by_key: dict[tuple[date, str, Decimal], list[BankLine]] = defaultdict(list)
    firefly_by_key: dict[tuple[date, str, Decimal], list[FireflyLine]] = defaultdict(
        list
    )
    for row in bank:
        bank_by_key[row.date, row.currency, Decimal(row.amount)].append(row)
    for tx, movement in live.values():
        firefly_by_key[tx.date, tx.currency.code, movement].append(
            line_for(tx, movement)
        )

    groups = []
    for day, currency, amount in sorted(bank_by_key.keys() | firefly_by_key.keys()):
        left = bank_by_key[day, currency, amount]
        right = sorted(firefly_by_key[day, currency, amount], key=lambda line: line.id)
        # Use the statement's formatting for display when it supplies an amount.
        amount_text = left[0].amount if left else right[0].amount
        group_id = hashlib.sha256(
            json.dumps(
                [account_id, day.isoformat(), currency, amount_key(amount)]
            ).encode()
        ).hexdigest()
        contains_transfer = any(line.type == "transfer" for line in right)
        bank_titles = Counter(normalize_title(row.description) for row in left)
        firefly_titles = Counter(normalize_title(line.description) for line in right)
        groups.append(
            ReconciliationGroup(
                id=group_id,
                date=day,
                currency=currency,
                amount=amount_text,
                bank=[
                    OperationEvidence(
                        id=row.row_id, date=row.date, description=row.description
                    )
                    for row in left
                ],
                firefly=right,
                exact_description_count=sum((bank_titles & firefly_titles).values()),
                contains_transfer=contains_transfer,
                eligible_ids=[line.id for line in right if line.can_mark]
                if len(left) == len(right) and not contains_transfer
                else [],
            )
        )
    return groups


def compare_statement(
    bank: Sequence[BankLine],
    transactions: list[SimplifiedTx],
    *,
    account_id: int,
    date_from: date,
    date_to: date,
    tolerance_days: int,
    file_hash: str,
    skipped_multipart: int = 0,
    skipped_invalid: int = 0,
    include_operation_groups: bool = True,
) -> ReconciliationPreview:
    in_period = [row for row in bank if date_from <= row.date <= date_to]
    if not in_period:
        raise ValueError("No statement transactions within the selected period")
    selected = [row for row in in_period if not Decimal(row.amount).is_zero()]
    live = {}
    excluded = 0
    own_iban = None
    for tx in transactions:
        if not date_from <= tx.date <= date_to:
            continue
        for account in (tx.source_account, tx.destination_account):
            if account and account.id == account_id and account.type.value == "asset":
                own_iban = account.iban or own_iban
        movement = account_movement(tx, account_id)
        if movement is None:
            if (
                tx.destination_account
                and tx.destination_account.id == account_id
                and tx.type.value == "transfer"
                and tx.fx
            ):
                excluded += 1
            continue
        live[tx.id] = (tx, movement)
    # Verify the bank's account column when both sides supply an account number.
    if own_iban and any(
        row.own_account and account_number(row.own_account) != account_number(own_iban)
        for row in selected
    ):
        raise ValueError(
            "Statement account does not match the selected Firefly account"
        )
    buckets: dict[tuple[str, Decimal], list[SimplifiedTx]] = defaultdict(list)
    for tx, movement in live.values():
        buckets[(tx.currency.code, movement)].append(tx)
    weak = {}
    strong = {}
    reverse: dict[int, list[int]] = defaultdict(list)
    weak_count: dict[int, int] = defaultdict(int)
    for row in selected:
        candidates = [
            tx
            for tx in buckets[(row.currency, Decimal(row.amount))]
            if abs((row.date - tx.date).days) <= tolerance_days
        ]
        weak[row.row_id] = candidates
        for tx in candidates:
            weak_count[tx.id] += 1
        strong[row.row_id] = [
            tx
            for tx in candidates
            if normalize_title(tx.description) == normalize_title(row.description)
        ]
        for tx in strong[row.row_id]:
            reverse[tx.id].append(row.row_id)
    operation_groups = (
        group_operations(selected, live, tolerance_days)
        if include_operation_groups
        else []
    )
    description_pairs = {
        group.bank[0].id: (group.firefly[0].id, group.compared_key)
        for group in operation_groups
        if group.reason == "card_prefix" and len(group.bank) == len(group.firefly) == 1
    }
    rows = []
    matched_ids = set()
    review_ids: set[int] = set()
    for row in selected:
        exact = strong[row.row_id]
        matched = len(exact) == 1 and len(reverse[exact[0].id]) == 1
        exact_ids = {tx.id for tx in exact}
        candidates = (
            exact
            if matched
            else exact + [tx for tx in weak[row.row_id] if tx.id not in exact_ids]
        )
        suggestion_id, suggestion_key = description_pairs.get(row.row_id, (None, None))
        if exact or (suggestion_id is not None and reverse[suggestion_id]):
            suggestion_id, suggestion_key = None, None
        if suggestion_id is not None:
            candidates.sort(key=lambda tx: tx.id != suggestion_id)
        status = "matched" if matched else "review" if candidates else "bank_only"
        if matched:
            matched_ids.add(exact[0].id)
        else:
            review_ids.update(tx.id for tx in candidates)
        rows.append(
            ComparisonRow(
                bank=row,
                status=status,
                candidates=[
                    line_for(
                        tx,
                        live[tx.id][1],
                        title_matches=tx.id in exact_ids,
                        normalized_description_key=(
                            suggestion_key if tx.id == suggestion_id else None
                        ),
                    )
                    for tx in candidates[:5]
                ],
                candidate_count=len(candidates),
                review_reason=(
                    None
                    if status != "review"
                    else "description_normalization"
                    if suggestion_id is not None
                    else "title_mismatch"
                    if not exact
                    and len(candidates) == 1
                    and weak_count[candidates[0].id] == 1
                    else "ambiguous"
                ),
            )
        )
    bank_totals: dict[str, Decimal] = defaultdict(Decimal)
    firefly_totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in selected:
        bank_totals[row.currency] += Decimal(row.amount)
    for tx, movement in live.values():
        firefly_totals[tx.currency.code] += movement
    revision_data = [
        file_hash,
        account_id,
        date_from.isoformat(),
        date_to.isoformat(),
        tolerance_days,
        sorted((tx.id, fingerprint(tx)) for tx, _ in live.values()),
        skipped_multipart,
        skipped_invalid,
        excluded,
    ]
    revision = hashlib.sha256(
        json.dumps(revision_data, sort_keys=True).encode()
    ).hexdigest()
    return ReconciliationPreview(
        revision=revision,
        rows=rows,
        firefly_only=[
            line_for(tx, movement)
            for tx, movement in live.values()
            if tx.id not in matched_ids | review_ids
        ],
        totals=[
            CurrencyDifference(
                currency=currency,
                bank_total=str(bank_totals[currency]),
                firefly_total=str(firefly_totals[currency]),
                difference=str(bank_totals[currency] - firefly_totals[currency]),
            )
            for currency in sorted(bank_totals.keys() | firefly_totals.keys())
        ],
        skipped_multipart=skipped_multipart,
        skipped_invalid=skipped_invalid,
        skipped_zero_amount=len(in_period) - len(selected),
        excluded_foreign_transfers=excluded,
        operation_groups=operation_groups,
        reconciliation_groups=reconciliation_groups(selected, live, account_id),
    )
