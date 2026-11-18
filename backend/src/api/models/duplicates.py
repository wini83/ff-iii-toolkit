from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from api.models.tx import SimplifiedAccountRef, SimplifiedTx


class DuplicateOptions(BaseModel):
    accounts: list[SimplifiedAccountRef]
    date_from: date | None
    date_to: date | None
    snapshot_timestamp: datetime
    skipped_multipart: int


class DuplicatePairResponse(BaseModel):
    id: str
    first: SimplifiedTx
    second: SimplifiedTx
    amount: str
    currency_code: str
    direction: Literal["incoming", "outgoing"]
    date_difference_days: int


class DuplicateResults(BaseModel):
    items: list[DuplicatePairResponse]
    total: int
    offset: int
    limit: int
    snapshot_timestamp: datetime
    firefly_url: str | None
    skipped_multipart: int
