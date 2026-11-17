from services.domain.metrics import BlikStatisticsMetrics
from services.jobs.metrics import MetricsManager
from services.jobs.runtime import JobRuntime
from services.tx_stats.runner import MetricsProvider


class BlikMetricsManager(MetricsManager[BlikStatisticsMetrics]):
    def __init__(
        self,
        *,
        provider: MetricsProvider[BlikStatisticsMetrics],
        runtime: JobRuntime | None = None,
    ):
        super().__init__(
            provider=provider,
            model=BlikStatisticsMetrics,
            feature="blik",
            runtime=runtime,
        )
