"""Conservative one-to-one matches; amount-only similarities are review items."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from decimal import Decimal

from ff_iii_luciferin.domain.models import SimplifiedTx

from services.reconciliation.models import (
    BankLine,
    ComparisonRow,
    CurrencyDifference,
    FireflyLine,
    ReconciliationPreview,
)


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
    return -abs(tx.amount) if outgoing else abs(tx.amount)


def fingerprint(tx: SimplifiedTx) -> str:
    """Ignore reconciliation state so a retry can safely skip completed writes."""
    data = {
        "id": tx.id,
        "journal": tx.transaction_journal_id,
        "date": tx.date.isoformat(),
        "amount": str(tx.amount.normalize()),
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
    tx: SimplifiedTx, movement: Decimal, *, title_matches: bool | None = None
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
        can_mark=restriction is None,
        restriction=restriction,
        title_matches=title_matches,
    )


def account_number(value: str) -> str:
    return re.sub(r"\s+", "", value).upper().removeprefix("PL")


def compare_statement(
    bank: list[BankLine],
    transactions: list[SimplifiedTx],
    *,
    account_id: int,
    date_from: date,
    date_to: date,
    tolerance_days: int,
    file_hash: str,
    skipped_multipart: int = 0,
    skipped_invalid: int = 0,
) -> ReconciliationPreview:
    selected = [row for row in bank if date_from <= row.date <= date_to]
    if not selected:
        raise ValueError("No statement transactions within the selected period")
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
                    line_for(tx, live[tx.id][1], title_matches=tx.id in exact_ids)
                    for tx in candidates[:5]
                ],
                candidate_count=len(candidates),
                review_reason=(
                    None
                    if status != "review"
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
                bank_total=format(bank_totals[currency], "f"),
                firefly_total=format(firefly_totals[currency], "f"),
                difference=format(
                    bank_totals[currency] - firefly_totals[currency], "f"
                ),
            )
            for currency in sorted(bank_totals.keys() | firefly_totals.keys())
        ],
        skipped_multipart=skipped_multipart,
        skipped_invalid=skipped_invalid,
        excluded_foreign_transfers=excluded,
    )
