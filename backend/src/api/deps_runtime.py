"""Runtime-scoped dependencies (process singletons)."""

from functools import lru_cache

from fastapi import Depends, Request

from api.deps_services import (
    get_allegro_service,
    get_firefly_enrichment_service,
    get_firefly_tx_service,
    get_snapshot_allegro_metrics_service,
    get_snapshot_blik_metrics_service,
    get_snapshot_tx_metrics_service,
    get_user_secrets_service,
)
from services.allegro_application_service import AllegroApplicationService
from services.allegro_service import AllegroService
from services.allegro_state_store import AllegroStateStore
from services.blik_application_service import BlikApplicationService
from services.blik_state_store import get_blik_state_store
from services.citi_import.cache import get_citi_import_store
from services.citi_import.exporter import CitiCsvZipExporter
from services.citi_import.mapper import CitiBankRecordMapper
from services.citi_import.parser import CitiTextParser
from services.citi_import.service import CitiImportService
from services.firefly_enrichment_service import FireflyEnrichmentService
from services.snapshot.metrics import (
    SnapshotAllegroMetricsService,
    SnapshotBlikMetricsService,
    SnapshotTxMetricsService,
)
from services.tx_application_service import TxApplicationService
from services.user_secrets_service import UserSecretsService
from services.velobank_import.session import VeloBankPreviewStore
from settings import settings


@lru_cache(maxsize=1)
def get_velobank_preview_store() -> VeloBankPreviewStore:
    return VeloBankPreviewStore()


async def get_blik_application_runtime(
    request: Request,
    metrics_provider: SnapshotBlikMetricsService = Depends(
        get_snapshot_blik_metrics_service
    ),
) -> BlikApplicationService:
    service = getattr(request.app.state, "blik_application_runtime", None)
    if service is None:
        service = BlikApplicationService(
            enrichment_service=get_firefly_enrichment_service(),
            metrics_provider=metrics_provider,
            state_store=get_blik_state_store(),
        )
        request.app.state.blik_application_runtime = service
    return service


async def get_tx_application_runtime(
    request: Request,
    metrics_provider: SnapshotTxMetricsService = Depends(
        get_snapshot_tx_metrics_service
    ),
) -> TxApplicationService:
    service = getattr(request.app.state, "tx_application_runtime", None)
    if service is None:
        service = TxApplicationService(
            tx_service=get_firefly_tx_service(),
            metrics_provider=metrics_provider,
        )
        request.app.state.tx_application_runtime = service
    return service


@lru_cache(maxsize=1)
def get_citi_import_runtime() -> CitiImportService:
    return CitiImportService(
        parser=CitiTextParser(),
        mapper=CitiBankRecordMapper(),
        store=get_citi_import_store(),
        exporter=CitiCsvZipExporter(),
    )


def get_allegro_runtime_state(request: Request) -> AllegroStateStore:
    return request.app.state.allegro_runtime_state


async def get_allegro_application_runtime(
    metrics_provider: SnapshotAllegroMetricsService = Depends(
        get_snapshot_allegro_metrics_service
    ),
    secrets_service: UserSecretsService = Depends(get_user_secrets_service),
    enrichment_service: FireflyEnrichmentService = Depends(
        get_firefly_enrichment_service
    ),
    allegro_service: AllegroService = Depends(get_allegro_service),
    state: AllegroStateStore = Depends(get_allegro_runtime_state),
) -> AllegroApplicationService:
    return AllegroApplicationService(
        secrets_service=secrets_service,
        enrichment_service=enrichment_service,
        metrics_provider=metrics_provider,
        allegro_service=allegro_service,
        state_store=state,
        filter_desc_allegro=getattr(settings, "ALLEGRO_DESCRIPTION_FILTER", "allegro"),
    )
