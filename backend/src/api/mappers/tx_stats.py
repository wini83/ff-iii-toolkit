from api.mappers.job_status import map_status
from api.models.tx_stats import (
    TxMetricsResultResponse,
    TxMetricsStatusResponse,
)
from services.domain.metrics import TXStatisticsMetrics
from services.tx_stats.models import MetricsState


def map_tx_state_to_response(
    state: MetricsState[TXStatisticsMetrics],
) -> TxMetricsStatusResponse:
    result = None
    if state.result is not None:
        result = TxMetricsResultResponse(
            single_part_transactions=state.result.single_part_transactions,
            uncategorized_transactions=state.result.uncategorized_transactions,
            blik_not_ok=state.result.blik_not_ok,
            action_req=state.result.action_req,
            allegro_not_ok=state.result.allegro_not_ok,
            categorizable=state.result.categorizable,
            categorizable_by_month=state.result.categorizable_by_month,
            single_part_amount=state.result.single_part_amount,
            uncategorized_amount=state.result.uncategorized_amount,
            blik_not_ok_amount=state.result.blik_not_ok_amount,
            action_req_amount=state.result.action_req_amount,
            allegro_not_ok_amount=state.result.allegro_not_ok_amount,
            categorizable_amount=state.result.categorizable_amount,
            categorizable_amount_by_month=state.result.categorizable_amount_by_month,
            time_stamp=state.result.time_stamp,
            fetch_seconds=state.result.fetching_duration_ms / 1000.0,
        )

    return TxMetricsStatusResponse(
        status=map_status(state.status),
        progress=state.progress,
        result=result,
        error=state.error,
    )
