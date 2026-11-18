from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from api.models.job_base import JobStatus
from api.models.provider_metrics import ProviderAmountMetricsResponse


class BlikMetricsResultResponse(BaseModel):
    single_part_transactions: int
    uncategorized_transactions: int
    filtered_by_description_exact: int
    not_processed_transactions: int
    not_processed_by_month: dict[str, int]
    time_stamp: datetime
    fetch_seconds: float
    amount_metrics: ProviderAmountMetricsResponse | None = None


class BlikMetricsStatusResponse(BaseModel):
    status: JobStatus
    progress: str | None
    result: BlikMetricsResultResponse | None
    error: str | None
