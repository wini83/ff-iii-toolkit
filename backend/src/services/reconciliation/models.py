"""Wire models for the stateless statement comparison workflow."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

OperationReason = Literal[
    "exact",
    "card_prefix",
    "overlap",
    "date_shift",
    "generic_blik",
    "different_descriptions",
    "bank_only",
    "firefly_only",
]


class BankLine(BaseModel):
    row_id: int
    date: date
    amount: str
    currency: str
    description: str
    own_account: str = ""


class StatementPeriod(BaseModel):
    date_from: date
    date_to: date
    source: Literal["metadata", "transactions"]


class FireflyLine(BaseModel):
    id: int
    date: date
    amount: str
    currency: str
    description: str
    type: str
    reconciled: bool | None
    can_mark: bool
    restriction: str | None = None
    title_matches: bool | None = None
    normalized_description_key: str | None = None


class ComparisonRow(BaseModel):
    bank: BankLine
    status: Literal["matched", "review", "bank_only"]
    candidates: list[FireflyLine]
    candidate_count: int
    review_reason: (
        Literal["title_mismatch", "ambiguous", "description_normalization"] | None
    ) = None


class CurrencyDifference(BaseModel):
    currency: str
    bank_total: str
    firefly_total: str
    difference: str


class OperationEvidence(BaseModel):
    id: int
    date: date
    description: str


class OperationGroup(BaseModel):
    amount: str
    currency: str
    bank: list[OperationEvidence]
    firefly: list[OperationEvidence]
    reason: OperationReason
    compared_key: str | None = None
    missing_count: int = 0
    excess_count: int = 0


class ReconciliationGroup(BaseModel):
    id: str
    date: date
    amount: str
    currency: str
    bank: list[OperationEvidence]
    firefly: list[FireflyLine]
    exact_description_count: int
    contains_transfer: bool
    eligible_ids: list[int]


class ReconciliationPreview(BaseModel):
    revision: str
    rows: list[ComparisonRow]
    firefly_only: list[FireflyLine]
    totals: list[CurrencyDifference]
    skipped_multipart: int
    skipped_invalid: int
    skipped_zero_amount: int
    excluded_foreign_transfers: int
    operation_groups: list[OperationGroup]
    reconciliation_groups: list[ReconciliationGroup]


class ReconciliationOutcome(BaseModel):
    id: int
    status: Literal["reconciled", "already_reconciled", "unknown", "not_processed"]


class ReconciliationApplyResult(BaseModel):
    outcomes: list[ReconciliationOutcome]
