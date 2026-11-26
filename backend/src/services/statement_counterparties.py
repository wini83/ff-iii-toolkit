"""Plan and apply counterparty corrections backed by a bank statement."""

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from ff_iii_luciferin.api import FireflyAPIError, FireflyClient, TransactionUpdate
from ff_iii_luciferin.domain.models import SimplifiedAccount, SimplifiedTx
from ff_iii_luciferin.services.transactions import fetch_transactions_with_stats
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from services.domain.blik import ApplyOutcome, BlikApplyJob
from services.domain.job_base import JobStatus
from services.jobs.apply import ApplyJobManager
from services.matching_cache.backend import MatchBackend
from services.reconciliation.matching import (
    account_movement,
    account_number,
    fingerprint,
    normalize_title,
)
from services.reconciliation.parser import StatementLine, parse_statement
from services.snapshot.store import SnapshotStore

UNKNOWN_ACCOUNTS = {
    "(unknown destination account)",
    "(unknown source account)",
    "unknown counterparty",
}
NON_COUNTERPARTY_TYPES = {"asset", "liability", "liabilities"}
CARD_PREFIXES = {
    "transakcja kartą debetową",
    "transakcja kartą debetową z cashback",
    "transakcja kartą kredytową",
}
Status = Literal["ready", "review", "skipped"]
MatchKey = tuple[date, str, Decimal]
AccountChoice = int | Literal["create"]


class StatementAccountOption(BaseModel):
    id: int
    name: str


class CounterpartyCandidate(StatementAccountOption):
    match: Literal["merchant", "statement", "both", "prefix", "generic"]
    prior_assignments: int = 0


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
    card_merchant: str | None = None
    candidates: list[CounterpartyCandidate] = Field(default_factory=list)
    choice_group: str | None = None
    new_account_name: str | None = None
    card_group_size: int | None = None


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


@dataclass(frozen=True)
class NewExpenseAssignment:
    tx: SimplifiedTx
    name: str


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


def card_merchant(description: str) -> str:
    prefix, _, merchant = normalize_title(description).partition(",")
    return merchant.strip() if prefix.strip() in CARD_PREFIXES else ""


def same_card_merchant(merchant: str, bank_title: str) -> bool:
    return bool(merchant) and (
        bank_title == merchant or bank_title.startswith(f"{merchant} ")
    )


def card_candidates(
    merchant: str,
    bank_title: str,
    name_index: dict[str, list[SimplifiedAccount]],
    history: Counter[tuple[str, int]],
) -> list[CounterpartyCandidate]:
    candidates = []
    prefix = merchant
    names = [merchant]
    for token in bank_title[len(merchant) :].split():
        prefix = f"{prefix} {token}"
        names.append(prefix)
    for name in names:
        match: Literal["merchant", "statement", "both", "prefix", "generic"]
        if name == merchant:
            match = "both" if name == bank_title else "merchant"
        else:
            match = "statement" if name == bank_title else "prefix"
        for account in name_index.get(name, []):
            candidates.append(
                CounterpartyCandidate(
                    id=account.id,
                    name=account.name,
                    match=match,
                    prior_assignments=history[merchant, account.id],
                )
            )
    if not any(candidate.match != "prefix" for candidate in candidates):
        tokens = merchant.split()
        for end in range(1, len(tokens)):
            for account in name_index.get(" ".join(tokens[:end]), []):
                candidates.append(
                    CounterpartyCandidate(
                        id=account.id,
                        name=account.name,
                        match="generic",
                        prior_assignments=history[merchant, account.id],
                    )
                )
    return sorted(
        candidates,
        key=lambda a: (a.match in {"prefix", "generic"}, -a.prior_assignments, a.id),
    )


def assignment_restriction(tx: SimplifiedTx, outgoing: bool) -> str | None:
    if tx.transaction_journal_id is None:
        return "Firefly did not provide a transaction journal ID."
    if "do-usuniecia" in tx.tags:
        return "The transaction is tagged do-usuniecia."
    if tx.type.value != ("withdrawal" if outgoing else "deposit"):
        return "The Firefly transaction type does not match the statement direction."
    current = tx.destination_account if outgoing else tx.source_account
    if current is None:
        return "Firefly did not provide the current counterparty account."
    if current.type.value != ("expense" if outgoing else "revenue"):
        return "The current Firefly counterparty has an incompatible account type."
    return None


def propose(
    bank: StatementLine,
    matches: list[SimplifiedTx],
    matching_rows: int,
    accounts: list[SimplifiedAccount],
    *,
    name_index: dict[str, list[SimplifiedAccount]] | None = None,
    card_history: Counter[tuple[str, int]] | None = None,
    account_verified: bool = True,
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

    by_name = name_index is not None
    merchant = ""
    exact_candidates: list[CounterpartyCandidate] = []
    if name_index is not None:
        merchant = card_merchant(tx.description)
        bank_title = normalize_title(bank.description)
        if not account_verified:
            row.status, row.reason = "review", "Could not verify the statement account."
            return row, None
        if not same_card_merchant(merchant, bank_title):
            row.status, row.reason = (
                "review",
                "The card merchant differs between the statement and Firefly.",
            )
            return row, None
        row.card_merchant = tx.description.partition(",")[2].strip() or merchant
        row.candidates = card_candidates(
            merchant,
            bank_title,
            name_index,
            card_history if card_history is not None else Counter(),
        )
        exact_candidates = [
            candidate
            for candidate in row.candidates
            if candidate.match not in {"prefix", "generic"}
        ]

    expected_type = "expense" if outgoing else "revenue"
    targets = [
        account
        for account in accounts
        if account.type == expected_type
        and account.active is True
        and account.name.strip().casefold() not in UNKNOWN_ACCOUNTS
    ]
    if not by_name and len(targets) > 1:
        row.status = "review"
        row.reason = "Several Firefly accounts have this number."
        return row, None
    target: StatementAccountOption | SimplifiedAccount | None
    if by_name:
        target = exact_candidates[0] if len(exact_candidates) == 1 else None
    else:
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
    if len(exact_candidates) > 1:
        row.status = "review"
        if restriction := assignment_restriction(tx, outgoing):
            row.reason = restriction
            return row, None
        account_ids = sorted(candidate.id for candidate in exact_candidates)
        row.choice_group = hashlib.sha256(
            f"{merchant}:{account_ids}".encode()
        ).hexdigest()
        row.reason = "Several Firefly accounts match this card merchant."
        return row, None
    if target is None:
        if by_name:
            row.status = "review"
            if restriction := assignment_restriction(tx, outgoing):
                row.reason = restriction
                return row, None
            row.reason = (
                "No exact Firefly expense account name matches this card merchant."
            )
            row.new_account_name = row.card_merchant
            row.choice_group = hashlib.sha256(f"{merchant}:create".encode()).hexdigest()
        else:
            row.reason = (
                "No active Firefly counterparty matches the statement account number."
            )
        return row, None
    row.proposed_account = target.name
    if not by_name and normalize_title(tx.description) != normalize_title(
        bank.description
    ):
        row.status, row.reason = (
            "review",
            "The statement and Firefly descriptions differ.",
        )
        return row, None
    if restriction := assignment_restriction(tx, outgoing):
        row.status, row.reason = "review", restriction
        return row, None
    row.status = "ready"
    row.reason = (
        "Matched to an existing expense account by card merchant."
        if by_name
        else "Matched to an existing account by number."
    )
    return row, Assignment(tx, target.id)


def matched_card_groups(
    bank_rows: list[StatementLine],
    transactions: list[SimplifiedTx],
    own: set[str],
    verified_single_account: bool,
) -> list[tuple[list[StatementLine], list[SimplifiedTx]]]:
    """Resolve same-day, same-amount collisions with card merchant evidence."""
    if len(bank_rows) < 2 and len(transactions) < 2:
        return []
    by_merchant: dict[str, list[SimplifiedTx]] = defaultdict(list)
    for tx in transactions:
        merchant = card_merchant(tx.description)
        if tx.type.value != "withdrawal" or not merchant:
            return []
        by_merchant[merchant].append(tx)
    statement_groups: dict[str, list[StatementLine]] = defaultdict(list)
    for row in bank_rows:
        if row.counterparty_account or not (
            canonical_number(row.own_account) in own
            or (not row.own_account and verified_single_account)
        ):
            return []
        title = normalize_title(row.description)
        candidates = [
            merchant for merchant in by_merchant if same_card_merchant(merchant, title)
        ]
        if len(candidates) != 1:
            return []
        statement_groups[candidates[0]].append(row)
    return [
        (rows, sorted(by_merchant[merchant], key=lambda tx: tx.id))
        for merchant, rows in statement_groups.items()
        if len(rows) == len(by_merchant[merchant])
    ]


def card_group_plan(
    bank_rows: list[StatementLine],
    transactions: list[SimplifiedTx],
    by_name: dict[str, list[SimplifiedAccount]],
    card_history: Counter[tuple[str, int]],
) -> dict[int, tuple[CounterpartyRow, Assignment | None, SimplifiedTx]]:
    results = [
        (*propose(bank, [tx], 1, [], name_index=by_name, card_history=card_history), tx)
        for bank, tx in zip(bank_rows, transactions, strict=True)
    ]
    if len(results) > 1:
        targets = {
            (
                row.status,
                assignment.account_id if assignment else None,
                row.choice_group,
            )
            for row, assignment, _ in results
        }
        writable = len(targets) == 1 and all(
            row.status == "ready" or row.choice_group is not None
            for row, _, _ in results
        )
        already_assigned = (
            all(
                row.status == "skipped"
                and row.reason == "Counterparty already assigned."
                for row, _, _ in results
            )
            and len(
                {
                    tx.destination_account.id if tx.destination_account else None
                    for tx in transactions
                }
            )
            == 1
        )
        if not (writable or already_assigned):
            return {}
        for row, _, _ in results:
            row.card_group_size = len(results)
    return {row.row_id: (row, assignment, tx) for row, assignment, tx in results}


def build_preview(
    bank: list[StatementLine],
    transactions: list[SimplifiedTx],
    accounts: list[SimplifiedAccount],
    account_id: int,
    file_digest: str,
    skipped_multipart: int,
    skipped_invalid: int,
    *,
    single_account_confirmed: bool = False,
) -> tuple[CounterpartyPreview, list[Assignment], dict[str, list[SimplifiedTx]]]:
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
    identified_numbers = {
        number for row in bank if (number := canonical_number(row.own_account))
    }
    if identified_numbers and identified_numbers.isdisjoint(own):
        raise ValueError(
            "The statement contains no rows for the selected bank account number."
        )
    # Numberless rows need an explicit single-account declaration and must
    # agree with every account number present in the statement.
    verified_single_account = (
        single_account_confirmed
        and identified_numbers <= own
        and all(
            not row.own_account or canonical_number(row.own_account) for row in bank
        )
    )

    # Rows with missing account numbers can still make a match ambiguous.
    rows_by_key: dict[MatchKey, list[StatementLine]] = defaultdict(list)
    for row in bank:
        number = canonical_number(row.own_account)
        if not number or number in own:
            rows_by_key[bank_key(row)].append(row)
    matches: dict[MatchKey, list[SimplifiedTx]] = defaultdict(list)
    card_history: Counter[tuple[str, int]] = Counter()
    for tx in transactions:
        movement = account_movement(tx, account_id)
        if movement is not None:
            matches[(tx.date, tx.currency.code, movement)].append(tx)
            if (
                tx.type.value == "withdrawal"
                and (merchant := card_merchant(tx.description))
                and tx.destination_account
                and "do-usuniecia" not in tx.tags
            ):
                card_history[merchant, tx.destination_account.id] += 1
    by_number: dict[str, list[SimplifiedAccount]] = defaultdict(list)
    by_name: dict[str, list[SimplifiedAccount]] = defaultdict(list)
    for account in accounts:
        for number in numbers(account):
            by_number[number].append(account)
        if (
            account.type == "expense"
            and account.active is True
            and normalize_title(account.name) not in UNKNOWN_ACCOUNTS
        ):
            by_name[normalize_title(account.name)].append(account)

    grouped: dict[int, tuple[CounterpartyRow, Assignment | None, SimplifiedTx]] = {}
    for key, bank_rows in rows_by_key.items():
        if key[2] >= 0 or not matches[key]:
            continue
        for group_rows, group_txs in matched_card_groups(
            bank_rows, matches[key], own, verified_single_account
        ):
            grouped.update(
                card_group_plan(group_rows, group_txs, by_name, card_history)
            )

    decisions: list[Assignment] = []
    rows: list[CounterpartyRow] = []
    choices: dict[str, list[SimplifiedTx]] = defaultdict(list)
    omitted = 0
    for line in bank:
        own_number = canonical_number(line.own_account)
        number = canonical_number(line.counterparty_account)
        key = bank_key(line)
        txs = matches[key]
        card = (
            not line.counterparty_account
            and key[2] < 0
            and any(card_merchant(tx.description) for tx in txs)
        )
        choice_tx = txs[0] if txs else None
        if line.row_id in grouped:
            row, assignment, choice_tx = grouped[line.row_id]
        elif card and (not line.own_account or own_number in own):
            row, assignment = propose(
                line,
                txs,
                len(rows_by_key[key]),
                [],
                name_index=by_name,
                card_history=card_history,
                account_verified=own_number in own or verified_single_account,
            )
        elif own_number in own and number:
            row, assignment = propose(
                line, txs, len(rows_by_key[key]), by_number[number]
            )
        else:
            omitted += 1
            continue
        rows.append(row)
        if assignment is not None:
            decisions.append(assignment)
        if row.choice_group is not None and choice_tx is not None:
            choices[row.choice_group].append(choice_tx)
    if not identified_numbers and not rows:
        raise ValueError(
            "The statement contains no rows for the selected bank account number."
        )
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
    revision = hashlib.sha256(
        f"{account_id}:{single_account_confirmed}:{file_digest}".encode()
    )
    revision.update(preview.model_dump_json().encode())
    for decision in decisions:
        revision.update(f"{fingerprint(decision.tx)}:{decision.account_id};".encode())
    for group, group_transactions in choices.items():
        for tx in group_transactions:
            revision.update(f"{group}:{fingerprint(tx)};".encode())
    preview.revision = revision.hexdigest()
    return preview, decisions, choices


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

    async def plan(
        self, content: bytes, account_id: int, *, single_account_confirmed: bool = False
    ) -> tuple[CounterpartyPreview, list[Assignment], dict[str, list[SimplifiedTx]]]:
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
            single_account_confirmed=single_account_confirmed,
        )

    async def start(
        self,
        owner: UUID,
        content: bytes,
        account_id: int,
        revision: str,
        *,
        single_account_confirmed: bool = False,
        account_choices: dict[str, AccountChoice] | None = None,
    ) -> BlikApplyJob:
        preview, decisions, choices = await self.plan(
            content, account_id, single_account_confirmed=single_account_confirmed
        )
        if preview.revision != revision:
            raise CounterpartyPreviewChanged(
                "The preview changed. Compare the statement again."
            )
        choice_rows = {
            row.choice_group: row
            for row in preview.rows
            if row.choice_group is not None
        }
        work: list[Assignment | NewExpenseAssignment] = list(decisions)
        for group, selection in (account_choices or {}).items():
            row = choice_rows.get(group)
            if row is None:
                raise ValueError("Selected account group is not in this preview.")
            if selection == "create":
                if not row.new_account_name:
                    raise ValueError(
                        "Account creation is not available for this group."
                    )
                work.extend(
                    NewExpenseAssignment(tx, row.new_account_name)
                    for tx in choices[group]
                )
            elif any(
                selection == candidate.id and candidate.match != "prefix"
                for candidate in row.candidates
            ):
                work.extend(Assignment(tx, selection) for tx in choices[group])
            else:
                raise ValueError("Selected account is not a candidate for this group.")
        if not work:
            raise ValueError("No account changes to apply.")
        job = await self.jobs.create(owner=owner, total=len(work), file_id=revision)
        self.jobs.runtime.spawn(self.jobs.execution(job), self.run(job, work))
        return job

    async def run(
        self, job: BlikApplyJob, decisions: list[Assignment | NewExpenseAssignment]
    ) -> None:
        try:
            await self._run(job, decisions)
        finally:
            self.jobs.executions.pop(str(job.id), None)

    async def _run(
        self, job: BlikApplyJob, decisions: list[Assignment | NewExpenseAssignment]
    ) -> None:
        job.status = JobStatus.RUNNING
        uncertain = False
        resolved_accounts: dict[str, int] = {}
        await self.jobs.save(job)
        for decision in decisions:
            await self.jobs.save(job, in_flight=decision.tx.id)
            outcome, uncertain = await self.apply_one(decision, resolved_accounts)
            job.results.append(outcome)
            if outcome.status == "success":
                job.applied += 1
            else:
                job.failed += 1
            await self.jobs.save(job, in_flight=decision.tx.id if uncertain else None)
            if uncertain:
                break

        if job.applied or uncertain or resolved_accounts:
            await self.matching_cache.invalidate_all()
            await self.snapshot.invalidate()
        job.status = JobStatus.FAILED if uncertain else JobStatus.DONE
        job.finished_at = datetime.now(UTC)
        await self.jobs.save(job)

    async def resolve_expense_account(self, name: str) -> int | None:
        accounts = await self.client.fetch_accounts(account_type="expense")
        matches = [
            account
            for account in accounts
            if account.type == "expense"
            and normalize_title(account.name) == normalize_title(name)
        ]
        if matches:
            if len(matches) == 1 and matches[0].active:
                return matches[0].id
            return None
        created = await self.client.create_account(name=name, account_type="expense")
        if (
            created.type == "expense"
            and created.active
            and normalize_title(created.name) == normalize_title(name)
        ):
            return created.id
        raise FireflyAPIError("Could not verify the new expense account.")

    async def apply_one(
        self,
        decision: Assignment | NewExpenseAssignment,
        resolved_accounts: dict[str, int],
    ) -> tuple[ApplyOutcome, bool]:
        tx_id = decision.tx.id
        try:
            current = await self.client.get_transaction(tx_id)
        except FireflyAPIError:
            return ApplyOutcome(
                tx_id, None, "failed", "Could not read the transaction."
            ), False
        if fingerprint(current) != fingerprint(decision.tx):
            return ApplyOutcome(
                tx_id, None, "failed", "Transaction changed since preview."
            ), False

        # Reconciliation can change independently of the counterparty preview.
        # Verify the write against the status read immediately before it.
        if isinstance(decision, NewExpenseAssignment):
            name_key = normalize_title(decision.name)
            try:
                target_id = resolved_accounts.get(name_key)
                if target_id is None:
                    target_id = await self.resolve_expense_account(decision.name)
            except FireflyAPIError:
                return ApplyOutcome(
                    tx_id,
                    None,
                    "failed",
                    "Could not verify account creation. Compare a fresh preview before retrying.",
                ), True
            if target_id is None:
                return ApplyOutcome(
                    tx_id,
                    None,
                    "failed",
                    "The expense account name is no longer unique and active.",
                ), False
            resolved_accounts[name_key] = target_id
            decision = Assignment(current, target_id)
        else:
            decision = replace(decision, tx=current)
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
