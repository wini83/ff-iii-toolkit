"""Wire models for the stateless statement comparison workflow."""

from datetime import date
from typing import Literal

from pydantic import BaseModel


class BankLine(BaseModel):
    row_id: int
    date: date
    amount: str
    currency: str
    description: str
    own_account: str = ""


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


class ComparisonRow(BaseModel):
    bank: BankLine
    status: Literal["matched", "review", "bank_only"]
    candidates: list[FireflyLine]
    candidate_count: int


class CurrencyDifference(BaseModel):
    currency: str
    bank_total: str
    firefly_total: str
    difference: str


class ReconciliationPreview(BaseModel):
    revision: str
    rows: list[ComparisonRow]
    firefly_only: list[FireflyLine]
    totals: list[CurrencyDifference]
    skipped_multipart: int
    skipped_invalid: int
    excluded_foreign_transfers: int


class ReconciliationOutcome(BaseModel):
    id: int
    status: Literal["reconciled", "already_reconciled", "unknown", "not_processed"]


class ReconciliationApplyResult(BaseModel):
    outcomes: list[ReconciliationOutcome]
