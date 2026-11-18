from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class BaseMetrics:
    total_transactions: int
    fetching_duration_ms: int


@dataclass
class FetchMetrics(BaseMetrics):
    invalid: int
    multipart: int


@dataclass
class ProviderAmountMetrics:
    eligible_amount: dict[str, Decimal]
    processed_amount: dict[str, Decimal]
    not_processed_amount: dict[str, Decimal]
    processing_coverage: dict[str, Decimal | None]
    not_processed_amount_by_month: dict[str, dict[str, Decimal]]


@dataclass
class BlikStatisticsMetrics(BaseMetrics):
    single_part_transactions: int
    uncategorized_transactions: int
    filtered_by_description_exact: int
    not_processed_transactions: int
    not_processed_by_month: dict[str, int]
    time_stamp: datetime
    amount_metrics: ProviderAmountMetrics | None = None


@dataclass
class AllegroMetrics(BaseMetrics):
    allegro_transactions: int
    not_processed_allegro_transactions: int
    not_processed_by_month: dict[str, int]
    time_stamp: datetime
    amount_metrics: ProviderAmountMetrics | None = None


@dataclass
class TXStatisticsMetrics(BaseMetrics):
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
