from functools import lru_cache

from fastapi import Cookie, Depends, Header, Request
from ff_iii_luciferin.api import FireflyClient
from sqlalchemy.orm import Session

from api.deps_db import get_db
from services.allegro_service import AllegroService, allegro_client_factory
from services.categorization import (
    AmountBucketizer,
    CategorizationTextPreprocessor,
    DefaultCategorySuggestionService,
    SnapshotCategorizationProvider,
    WeightedTransactionSimilarityEngine,
)
from services.categorization.service import CategorySuggestionService
from services.db.repository import (
    AuditLogRepository,
    UserRepository,
    UserSecretRepository,
    UserSecretVaultRepository,
)
from services.firefly_base_service import FireflyBaseService
from services.firefly_enrichment_service import FireflyEnrichmentService
from services.firefly_tx_service import FireflyTxService
from services.secret_crypto_service import SecretCryptoService
from services.snapshot import (
    SnapshotAllegroMetricsService,
    SnapshotBlikMetricsService,
    SnapshotStore,
    SnapshotTxMetricsService,
    TransactionSnapshotService,
)
from services.snapshot.coordination import RedisRefreshCoordinator
from services.snapshot.redis_store import RedisSnapshotStore
from services.system.bootstrap import BootstrapService
from services.user_secrets_service import UserSecretsService
from services.vault_service import VaultService
from services.vault_session_store import VaultSessionStore
from settings import settings


@lru_cache(maxsize=1)
def get_firefly_client() -> FireflyClient:
    if not settings.FIREFLY_URL or not settings.FIREFLY_TOKEN:
        raise RuntimeError("Config error")

    return FireflyClient(
        base_url=settings.FIREFLY_URL,
        token=settings.FIREFLY_TOKEN,
    )


@lru_cache(maxsize=1)
def get_firefly_base_service() -> FireflyBaseService:
    client = get_firefly_client()
    return FireflyBaseService(client)


@lru_cache(maxsize=1)
def get_firefly_enrichment_service() -> FireflyEnrichmentService:
    client = get_firefly_client()
    return FireflyEnrichmentService(client)


@lru_cache(maxsize=1)
def get_firefly_tx_service() -> FireflyTxService:
    client = get_firefly_client()
    return FireflyTxService(
        client,
        settings.BLIK_DESCRIPTION_FILTER,
        getattr(settings, "ALLEGRO_DESCRIPTION_FILTER", "allegro"),
    )


def get_snapshot_store(request: Request) -> SnapshotStore:
    return request.app.state.snapshot_store


async def get_transaction_snapshot_service(
    request: Request,
) -> TransactionSnapshotService:
    service = getattr(request.app.state, "transaction_snapshot_service", None)
    if service is None:
        store = get_snapshot_store(request)
        coordinator = (
            RedisRefreshCoordinator(
                store,
                lease_seconds=settings.TRANSACTION_SNAPSHOT_REFRESH_LEASE_SECONDS,
                wait_timeout_seconds=settings.TRANSACTION_SNAPSHOT_REFRESH_TIMEOUT_SECONDS,
            )
            if isinstance(store, RedisSnapshotStore)
            else None
        )
        service = TransactionSnapshotService(
            store=store,
            coordinator=coordinator,
            firefly_service=get_firefly_base_service(),
            max_age_seconds=settings.TRANSACTION_SNAPSHOT_TTL_SECONDS,
        )
        request.app.state.transaction_snapshot_service = service
    return service


def get_snapshot_blik_metrics_service(
    snapshot_service: TransactionSnapshotService = Depends(
        get_transaction_snapshot_service
    ),
) -> SnapshotBlikMetricsService:
    return SnapshotBlikMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_blik=settings.BLIK_DESCRIPTION_FILTER,
    )


def get_snapshot_allegro_metrics_service(
    snapshot_service: TransactionSnapshotService = Depends(
        get_transaction_snapshot_service
    ),
) -> SnapshotAllegroMetricsService:
    return SnapshotAllegroMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_allegro=getattr(settings, "ALLEGRO_DESCRIPTION_FILTER", "allegro"),
    )


def get_snapshot_tx_metrics_service(
    snapshot_service: TransactionSnapshotService = Depends(
        get_transaction_snapshot_service
    ),
) -> SnapshotTxMetricsService:
    return SnapshotTxMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_blik=settings.BLIK_DESCRIPTION_FILTER,
        filter_desc_allegro=getattr(settings, "ALLEGRO_DESCRIPTION_FILTER", "allegro"),
    )


def get_category_suggestion_service(
    snapshot_service: TransactionSnapshotService = Depends(
        get_transaction_snapshot_service
    ),
) -> CategorySuggestionService:
    amount_bucketizer = AmountBucketizer()
    snapshot_provider = SnapshotCategorizationProvider(
        snapshot_service=snapshot_service,
        amount_bucketizer=amount_bucketizer,
    )
    similarity_engine = WeightedTransactionSimilarityEngine(
        preprocessor=CategorizationTextPreprocessor(),
        amount_bucketizer=amount_bucketizer,
    )
    return DefaultCategorySuggestionService(
        snapshot_provider=snapshot_provider,
        similarity_engine=similarity_engine,
        amount_bucketizer=amount_bucketizer,
    )


@lru_cache(maxsize=1)
def get_secret_crypto_service() -> SecretCryptoService:
    return SecretCryptoService()


@lru_cache(maxsize=1)
def get_vault_session_store() -> VaultSessionStore:
    return VaultSessionStore()


def get_vault_service(
    db: Session = Depends(get_db),
) -> VaultService:
    return VaultService(
        vault_repo=UserSecretVaultRepository(db),
        session_store=get_vault_session_store(),
        crypto_service=get_secret_crypto_service(),
        audit_repo=AuditLogRepository(db),
        vault_session_ttl_seconds=settings.VAULT_SESSION_TTL_SECONDS,
    )


def get_vault_session_id(
    vault_session_cookie: str | None = Cookie(
        default=None,
        alias=settings.VAULT_SESSION_COOKIE_NAME,
    ),
    vault_session_header: str | None = Header(
        default=None,
        alias="X-Vault-Session-Id",
    ),
) -> str | None:
    return vault_session_cookie or vault_session_header


def get_user_secrets_service(
    db: Session = Depends(get_db),
    vault_service: VaultService = Depends(get_vault_service),
) -> UserSecretsService:
    return UserSecretsService(
        secret_repo=UserSecretRepository(db),
        audit_repo=AuditLogRepository(db),
        vault_service=vault_service,
        crypto_service=get_secret_crypto_service(),
    )


def get_bootstrap_service(
    db: Session = Depends(get_db),
) -> BootstrapService:
    return BootstrapService(
        user_repo=UserRepository(db),
    )


def get_allegro_service() -> AllegroService:
    return AllegroService(client_factory=allegro_client_factory)
