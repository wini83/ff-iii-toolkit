from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from api.models.job_base import JobStatus


class RunResponse(BaseModel):
    job_id: str


class TxMetricsResultResponse(BaseModel):
    single_part_transactions: int
    uncategorized_transactions: int
    blik_not_ok: int
    action_req: int
    allegro_not_ok: int
    categorizable: int
    categorizable_by_month: dict[str, int]
    single_part_amount: dict[str, Decimal]
    uncategorized_amount: dict[str, Decimal]
    blik_not_ok_amount: dict[str, Decimal]
    action_req_amount: dict[str, Decimal]
    allegro_not_ok_amount: dict[str, Decimal]
    categorizable_amount: dict[str, Decimal]
    categorizable_amount_by_month: dict[str, dict[str, Decimal]]
    time_stamp: datetime
    fetch_seconds: float


class TxMetricsStatusResponse(BaseModel):
    status: JobStatus
    progress: str | None
    result: TxMetricsResultResponse | None
    error: str | None
