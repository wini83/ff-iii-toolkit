"""Fresh read, compare and narrowly update verified transaction flags."""

import hashlib
from datetime import date

from ff_iii_luciferin.api import FireflyClient
from ff_iii_luciferin.api.transaction_update import TransactionUpdate
from ff_iii_luciferin.domain.models import SimplifiedTx
from ff_iii_luciferin.services.transactions import fetch_transactions_with_stats
from starlette.concurrency import run_in_threadpool

from services.reconciliation.matching import compare_statement, fingerprint
from services.reconciliation.models import (
    ReconciliationApplyResult,
    ReconciliationOutcome,
    ReconciliationPreview,
)
from services.reconciliation.parser import parse_statement


class ComparisonChanged(ValueError):
    """The selected evidence no longer agrees with fresh Firefly data."""


class ReconciliationService:
    def __init__(self, client: FireflyClient):
        self.client = client

    async def compare(
        self, content: bytes, account_id: int, start: date, end: date, tolerance: int
    ) -> tuple[ReconciliationPreview, dict[int, SimplifiedTx]]:
        bank = await run_in_threadpool(parse_statement, content)
        transactions, stats = await fetch_transactions_with_stats(
            self.client, tx_type="all", start_date=start, end_date=end
        )
        preview = await run_in_threadpool(
            compare_statement,
            bank,
            transactions,
            account_id=account_id,
            date_from=start,
            date_to=end,
            tolerance_days=tolerance,
            file_hash=hashlib.sha256(content).hexdigest(),
            skipped_multipart=stats.multipart,
            skipped_invalid=stats.invalid,
        )
        return preview, {tx.id: tx for tx in transactions}

    async def apply(
        self,
        content: bytes,
        account_id: int,
        start: date,
        end: date,
        tolerance: int,
        revision: str,
        selected: list[int],
    ) -> ReconciliationApplyResult:
        preview, transactions = await self.compare(
            content, account_id, start, end, tolerance
        )
        if preview.revision != revision:
            raise ComparisonChanged(
                "Comparison changed. Compare the statement again before marking."
            )
        allowed = {
            row.candidates[0].id
            for row in preview.rows
            if row.status == "matched" and row.candidates[0].can_mark
        }
        if (
            not selected
            or len(selected) > 100
            or len(set(selected)) != len(selected)
            or not set(selected) <= allowed
        ):
            raise ValueError("Select 1–100 distinct, unambiguous, supported matches")
        # Validate the complete batch before its first write.
        current = {}
        for tx_id in selected:
            tx = await self.client.get_transaction(tx_id)
            if fingerprint(tx) != fingerprint(transactions[tx_id]):
                raise ComparisonChanged("Selected transaction changed. Compare again.")
            current[tx_id] = tx
        outcomes = []
        stopped = False
        for tx_id in selected:
            if stopped:
                outcomes.append(ReconciliationOutcome(id=tx_id, status="not_processed"))
                continue
            if current[tx_id].reconciled is True:
                outcomes.append(
                    ReconciliationOutcome(id=tx_id, status="already_reconciled")
                )
                continue
            try:
                # Recheck immediately before each write; Firefly has no atomic
                # conditional-update API, so this is not a lock against edits.
                before = await self.client.get_transaction(tx_id)
                if fingerprint(before) != fingerprint(current[tx_id]):
                    stopped = True
                    outcomes.append(
                        ReconciliationOutcome(id=tx_id, status="not_processed")
                    )
                    continue
                if before.reconciled is not True:
                    await self.client.update_transaction(
                        tx_id,
                        TransactionUpdate(reconciled=True),
                        apply_rules=False,
                        fire_webhooks=False,
                    )
            except Exception:
                # A failed response does not prove that the flag was not saved.
                pass
            try:
                after = await self.client.get_transaction(tx_id)
                verified = after.reconciled is True and fingerprint(
                    after
                ) == fingerprint(current[tx_id])
            except Exception:
                verified = False
            outcomes.append(
                ReconciliationOutcome(
                    id=tx_id, status="reconciled" if verified else "unknown"
                )
            )
            stopped = not verified
        return ReconciliationApplyResult(outcomes=outcomes)
