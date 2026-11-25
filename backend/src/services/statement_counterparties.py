"""Plan and apply counterparty corrections backed by a bank statement."""

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from ff_iii_luciferin.api import FireflyAPIError, FireflyClient, TransactionUpdate
from ff_iii_luciferin.domain.models import SimplifiedAccount, SimplifiedTx
from ff_iii_luciferin.services.transactions import fetch_transactions_with_stats
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from services.domain.blik import ApplyOutcome, BlikApplyJob
from services.domain.job_base import JobStatus
from services.jobs.apply import ApplyJobManager
from services.matching_cache.backend import MatchBackend
from services.reconciliation.matching import (
    account_movement,
    account_number,
    fingerprint,
)
from services.reconciliation.parser import StatementLine, parse_statement
from services.snapshot.store import SnapshotStore

UNKNOWN_ACCOUNTS = {
    "(unknown destination account)",
    "(unknown source account)",
    "unknown counterparty",
}
NON_COUNTERPARTY_TYPES = {"asset", "liability", "liabilities"}
Status = Literal["ready", "review", "skipped"]
MatchKey = tuple[date, str, Decimal]


class StatementAccountOption(BaseModel):
    id: int
    name: str


class CounterpartyRow(BaseModel):
    row_id: int
    date: date
    amount: str
    currency: str
    description: str
    status: Status = "skipped"
    reason: str = ""
    transaction_id: int | None = None
    firefly_description: str | None = None
    current_account: str | None = None
    proposed_account: str | None = None


class CounterpartyPreview(BaseModel):
    revision: str
    rows: list[CounterpartyRow]
    ready: int
    review: int
    skipped: int
    omitted: int
    skipped_multipart: int
    skipped_invalid: int


class CounterpartyPreviewChanged(ValueError):
    pass


@dataclass(frozen=True)
class Assignment:
    tx: SimplifiedTx
    account_id: int

    def update(self) -> TransactionUpdate:
        if self.tx.type.value == "withdrawal":
            return TransactionUpdate(destination_id=self.account_id)
        return TransactionUpdate(source_id=self.account_id)

    def verified(self, after: SimplifiedTx) -> bool:
        if self.tx.type.value == "withdrawal":
            account = after.destination_account
            restored = replace(after, destination_account=self.tx.destination_account)
        else:
            account = after.source_account
            restored = replace(after, source_account=self.tx.source_account)
        return (
            account is not None
            and account.id == self.account_id
            and after.reconciled == self.tx.reconciled
            and fingerprint(restored) == fingerprint(self.tx)
        )


def canonical_number(raw: str) -> str:
    number = account_number(raw)
    if re.fullmatch(r"[0-9]{26}|[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", number):
        return number
    return ""


def numbers(account: SimplifiedAccount) -> set[str]:
    return {
        number
        for value in (account.iban, account.account_number)
        if value and (number := canonical_number(value))
    }


def account_options(accounts: list[SimplifiedAccount]) -> list[StatementAccountOption]:
    return [
        StatementAccountOption(id=account.id, name=account.name)
        for account in sorted(accounts, key=lambda a: (a.name.casefold(), a.id))
        if account.type == "asset" and account.active is True and numbers(account)
    ]


def bank_key(row: StatementLine) -> MatchKey:
    return row.date, row.currency, Decimal(row.amount)


def propose(
    bank: StatementLine,
    matches: list[SimplifiedTx],
    matching_rows: int,
    accounts: list[SimplifiedAccount],
) -> tuple[CounterpartyRow, Assignment | None]:
    row = CounterpartyRow(
        row_id=bank.row_id,
        date=bank.date,
        amount=bank.amount,
        currency=bank.currency,
        description=bank.description,
    )
    own_targets = [
        account for account in accounts if account.type in NON_COUNTERPARTY_TYPES
    ]
    if len(matches) != 1 or matching_rows != 1:
        if own_targets:
            row.status = "review"
            row.reason = (
                "Possible transfer between your accounts. No unique Firefly match."
            )
            if len(own_targets) == 1:
                row.proposed_account = own_targets[0].name
        elif matches:
            row.status, row.reason = (
                "review",
                "Multiple transactions share this date and amount.",
            )
        else:
            row.reason = (
                "No Firefly transaction with the same booking date and signed amount."
            )
        return row, None

    tx = matches[0]
    outgoing = Decimal(bank.amount) < 0
    current = tx.destination_account if outgoing else tx.source_account
    row.transaction_id = tx.id
    row.firefly_description = tx.description
    row.current_account = current.name if current else None
    if own_targets or tx.type.value == "transfer":
        if (
            tx.type.value == "transfer"
            and current
            and any(target.id == current.id for target in own_targets)
        ):
            row.reason = (
                "Already recorded as a transfer to the account on the statement."
            )
        else:
            row.status, row.reason = (
                "review",
                "Possible transfer between your accounts.",
            )
            if len(own_targets) == 1:
                row.proposed_account = own_targets[0].name
        return row, None

    expected_type = "expense" if outgoing else "revenue"
    targets = [
        account
        for account in accounts
        if account.type == expected_type
        and account.active is True
        and account.name.strip().casefold() not in UNKNOWN_ACCOUNTS
    ]
    if len(targets) > 1:
        row.status, row.reason = "review", "Several Firefly accounts have this number."
        return row, None
    target = targets[0] if targets else None
    if current and target and current.id == target.id:
        row.reason = "Counterparty already assigned."
        return row, None
    if current and current.name.strip().casefold() not in UNKNOWN_ACCOUNTS:
        if target and current.id != target.id:
            row.status, row.reason = (
                "review",
                "An existing assignment differs from the statement.",
            )
            row.proposed_account = target.name
        else:
            row.reason = "Counterparty already assigned."
        return row, None
    if target is None:
        row.reason = (
            "No active Firefly counterparty matches the statement account number."
        )
        return row, None
    row.proposed_account = target.name
    if (
        tx.transaction_journal_id is None
        or tx.reconciled is not False
        or "do-usuniecia" in tx.tags
        or tx.type.value != ("withdrawal" if outgoing else "deposit")
        or not current
        or current.type.value != expected_type
    ):
        row.status, row.reason = "review", "Transaction requires manual review."
        return row, None
    row.status, row.reason = "ready", "Matched to an existing account by number."
    return row, Assignment(tx, target.id)


def build_preview(
    bank: list[StatementLine],
    transactions: list[SimplifiedTx],
    accounts: list[SimplifiedAccount],
    account_id: int,
    file_digest: str,
    skipped_multipart: int,
    skipped_invalid: int,
) -> tuple[CounterpartyPreview, list[Assignment]]:
    source = next((account for account in accounts if account.id == account_id), None)
    own = (
        numbers(source)
        if source and source.type == "asset" and source.active is True
        else set()
    )
    for account in accounts:
        if account.id != account_id and account.type == "asset":
            own.difference_update(numbers(account))
    if not own:
        raise ValueError(
            "Select an active Firefly asset account with a unique bank account number."
        )
    if not any(canonical_number(row.own_account) in own for row in bank):
        raise ValueError(
            "The statement contains no rows for the selected bank account number."
        )

    # Rows with missing account numbers can still make a match ambiguous.
    row_counts: dict[MatchKey, int] = defaultdict(int)
    for row in bank:
        number = canonical_number(row.own_account)
        if not number or number in own:
            row_counts[bank_key(row)] += 1
    matches: dict[MatchKey, list[SimplifiedTx]] = defaultdict(list)
    for tx in transactions:
        movement = account_movement(tx, account_id)
        if movement is not None:
            matches[(tx.date, tx.currency.code, movement)].append(tx)
    by_number: dict[str, list[SimplifiedAccount]] = defaultdict(list)
    for account in accounts:
        for number in numbers(account):
            by_number[number].append(account)

    decisions: list[Assignment] = []
    rows: list[CounterpartyRow] = []
    omitted = 0
    for line in bank:
        number = canonical_number(line.counterparty_account)
        if canonical_number(line.own_account) not in own or not number:
            omitted += 1
            continue
        key = bank_key(line)
        row, assignment = propose(
            line, matches[key], row_counts[key], by_number[number]
        )
        rows.append(row)
        if assignment is not None:
            decisions.append(assignment)
    preview = CounterpartyPreview(
        revision="",
        rows=rows,
        ready=len(decisions),
        review=sum(row.status == "review" for row in rows),
        skipped=omitted + sum(row.status == "skipped" for row in rows),
        omitted=omitted,
        skipped_multipart=skipped_multipart,
        skipped_invalid=skipped_invalid,
    )
    revision = hashlib.sha256(f"{account_id}:{file_digest}".encode())
    revision.update(preview.model_dump_json().encode())
    for decision in decisions:
        revision.update(f"{fingerprint(decision.tx)}:{decision.account_id};".encode())
    preview.revision = revision.hexdigest()
    return preview, decisions


class StatementCounterpartyService:
    def __init__(
        self,
        client: FireflyClient,
        jobs: ApplyJobManager[BlikApplyJob],
        matching_cache: MatchBackend,
        snapshot: SnapshotStore,
    ):
        self.client = client
        self.jobs = jobs
        self.matching_cache = matching_cache
        self.snapshot = snapshot

    async def plan(self, content: bytes, account_id: int):
        bank = await run_in_threadpool(parse_statement, content)
        start = min(row.date for row in bank)
        end = max(row.date for row in bank)
        if (end - start).days > 366:
            raise ValueError("Choose a statement of at most 366 days.")
        accounts = await self.client.fetch_accounts()
        transactions, stats = await fetch_transactions_with_stats(
            self.client, tx_type="all", start_date=start, end_date=end
        )
        return await run_in_threadpool(
            build_preview,
            bank,
            transactions,
            accounts,
            account_id,
            hashlib.sha256(content).hexdigest(),
            stats.multipart,
            stats.invalid,
        )

    async def start(
        self, owner: UUID, content: bytes, account_id: int, revision: str
    ) -> BlikApplyJob:
        preview, decisions = await self.plan(content, account_id)
        if preview.revision != revision:
            raise CounterpartyPreviewChanged(
                "The preview changed. Compare the statement again."
            )
        if not decisions:
            raise ValueError("No account changes to apply.")
        job = await self.jobs.create(
            owner=owner, total=len(decisions), file_id=revision
        )
        self.jobs.runtime.spawn(self.jobs.execution(job), self.run(job, decisions))
        return job

    async def run(self, job: BlikApplyJob, decisions: list[Assignment]) -> None:
        try:
            await self._run(job, decisions)
        finally:
            self.jobs.executions.pop(str(job.id), None)

    async def _run(self, job: BlikApplyJob, decisions: list[Assignment]) -> None:
        job.status = JobStatus.RUNNING
        uncertain = False
        await self.jobs.save(job)
        for decision in decisions:
            await self.jobs.save(job, in_flight=decision.tx.id)
            outcome, uncertain = await self.apply_one(decision)
            job.results.append(outcome)
            if outcome.status == "success":
                job.applied += 1
            else:
                job.failed += 1
            await self.jobs.save(job, in_flight=decision.tx.id if uncertain else None)
            if uncertain:
                break

        if job.applied or uncertain:
            await self.matching_cache.invalidate_all()
            await self.snapshot.invalidate()
        job.status = JobStatus.FAILED if uncertain else JobStatus.DONE
        job.finished_at = datetime.now(UTC)
        await self.jobs.save(job)

    async def apply_one(self, decision: Assignment) -> tuple[ApplyOutcome, bool]:
        tx_id = decision.tx.id
        try:
            current = await self.client.get_transaction(tx_id)
        except FireflyAPIError:
            return ApplyOutcome(
                tx_id, None, "failed", "Could not read the transaction."
            ), False
        if current.reconciled is not False or fingerprint(current) != fingerprint(
            decision.tx
        ):
            return ApplyOutcome(
                tx_id, None, "failed", "Transaction changed since preview."
            ), False

        try:
            after = await self.client.update_transaction(
                tx_id, decision.update(), apply_rules=False, fire_webhooks=False
            )
            if decision.verified(after):
                return ApplyOutcome(tx_id, None, "success"), False
        except FireflyAPIError:
            pass
        try:
            if decision.verified(await self.client.get_transaction(tx_id)):
                return ApplyOutcome(tx_id, None, "success"), False
        except FireflyAPIError:
            pass
        return ApplyOutcome(
            tx_id,
            None,
            "failed",
            "Could not verify the write. Compare a fresh preview before retrying.",
        ), True
