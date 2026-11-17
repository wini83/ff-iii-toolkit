from services.domain.metrics import AllegroMetrics
from services.jobs.metrics import MetricsManager
from services.jobs.runtime import JobRuntime
from services.tx_stats.runner import MetricsProvider


class AllegroMetricsManager(MetricsManager[AllegroMetrics]):
    def __init__(
        self,
        *,
        provider: MetricsProvider[AllegroMetrics],
        runtime: JobRuntime | None = None,
    ):
        super().__init__(
            provider=provider, model=AllegroMetrics, feature="allegro", runtime=runtime
        )
