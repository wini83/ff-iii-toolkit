from decimal import Decimal

from pydantic import BaseModel


class ProviderAmountMetricsResponse(BaseModel):
    eligible_amount: dict[str, Decimal]
    processed_amount: dict[str, Decimal]
    not_processed_amount: dict[str, Decimal]
    processing_coverage: dict[str, Decimal | None]
    not_processed_amount_by_month: dict[str, dict[str, Decimal]]
