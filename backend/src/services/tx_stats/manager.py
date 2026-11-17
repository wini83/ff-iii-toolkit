from services.domain.metrics import TXStatisticsMetrics
from services.jobs.metrics import MetricsManager
from services.jobs.runtime import JobRuntime
from services.tx_stats.runner import MetricsProvider


class TxMetricsManager(MetricsManager[TXStatisticsMetrics]):
    def __init__(
        self,
        *,
        provider: MetricsProvider[TXStatisticsMetrics],
        runtime: JobRuntime | None = None,
    ):
        super().__init__(
            provider=provider, model=TXStatisticsMetrics, feature="tx", runtime=runtime
        )
