"""Web import models keep monetary values as decimal strings."""

import re
from datetime import date as Date
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class VeloBankMappings(BaseModel):
    accounts: dict[str, str] = Field(default_factory=dict)

    @field_validator("accounts")
    @classmethod
    def normalize_accounts(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 100:
            raise ValueError("At most 100 own accounts may be mapped.")
        result = {}
        for raw, name in value.items():
            iban = re.sub(r"\s", "", raw).upper().removeprefix("PL")
            if (
                not re.fullmatch(r"\d{26}", iban)
                or not name.strip()
                or len(name.strip()) > 255
            ):
                raise ValueError(
                    "Map a Polish account number to a nonempty Firefly account name."
                )
            key = "PL" + iban
            if key in result:
                raise ValueError("The same account number was supplied twice.")
            result[key] = name.strip()
        return result


class VeloBankTotals(BaseModel):
    currency: str
    debits: str
    credits: str


class VeloBankRow(BaseModel):
    id: str
    date: Date
    booking_date: Date
    amount: str
    currency: str
    payee: str
    description: str
    source_account: str
    destination_account: str
    source_iban: str
    destination_iban: str
    needs_review: bool


class VeloBankPreviewResponse(BaseModel):
    file_id: UUID
    expires_at: datetime
    account_iban: str
    account_name: str
    start_date: Date
    end_date: Date
    record_count: int
    totals: list[VeloBankTotals]
    preview: list[VeloBankRow]
    warnings: list[str]


class VeloBankAccountOption(BaseModel):
    id: str
    name: str
    iban: str | None = None
