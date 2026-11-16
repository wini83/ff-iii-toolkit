import asyncio
import logging
import os
import tomllib
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routers.allegro import router as allegro_router
from api.routers.auth import router as auth_router
from api.routers.blik_files import router as blik_router
from api.routers.me import router as me_router
from api.routers.system import router as system_router
from api.routers.transactions import router as transactions_router
from api.routers.tx import router as tx_router
from api.routers.user_secrets import router as user_secrets_router
from api.routers.users import router as users_router
from api.routers.velobank_import import router as velobank_import_router
from middleware import register_middlewares
from services.allegro_state_store import AllegroStateStore
from services.blik_state_store import BlikStateStore
from services.db.engine import (
    create_engine_from_url,
    create_session_factory,
)
from services.db.init import DatabaseBootstrap
from services.infrastructure.redis import redis_connection
from services.matching_cache.backend import (
    MatchCacheUnavailable,
    MemoryMatchBackend,
    RedisMatchBackend,
)
from services.matching_cache.stores import AllegroMatchStore, BlikMatchStore
from services.snapshot.redis_store import RedisSnapshotStore, SnapshotStoreUnavailable
from services.snapshot.store import InMemorySnapshotStore, SnapshotStore
from services.velobank_import.redis_store import RedisVeloBankPreviewStore
from services.velobank_import.session import (
    PreviewStoreUnavailable,
    PreviewTooLarge,
    VeloBankPreviewStore,
)
from settings import settings
from utils.logger import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def get_version() -> str:
    release_version = os.getenv("TOOLKIT_VERSION")
    if release_version:
        return release_version

    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


# ==================================================
# Application factory – ZERO side effects
# ==================================================


def create_app(
    *,
    bootstrap: DatabaseBootstrap | None = None,
    snapshot_store: SnapshotStore | None = None,
) -> FastAPI:
    version = get_version()
    app = FastAPI(
        title="Firefly III Toolkit",
        version=version,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.state.version = version
    app.state.snapshot_store = snapshot_store or InMemorySnapshotStore()

    @app.exception_handler(SnapshotStoreUnavailable)
    async def snapshot_unavailable(request: Request, exc: SnapshotStoreUnavailable):
        return JSONResponse(
            status_code=503,
            content={"detail": "Transaction snapshot cache unavailable"},
        )

    @app.exception_handler(MatchCacheUnavailable)
    async def matching_unavailable(request: Request, exc: MatchCacheUnavailable):
        return JSONResponse(
            status_code=503, content={"detail": "Matching cache unavailable"}
        )

    @app.exception_handler(PreviewStoreUnavailable)
    async def preview_unavailable(request: Request, exc: PreviewStoreUnavailable):
        return JSONResponse(
            status_code=503, content={"detail": "Preview storage unavailable"}
        )

    @app.exception_handler(PreviewTooLarge)
    async def preview_too_large(request: Request, exc: PreviewTooLarge):
        return JSONResponse(
            status_code=413,
            content={"detail": "Preview exceeds the storage size limit"},
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if bootstrap:
            bootstrap.run()

        async with redis_connection(settings.REDIS_URL) as redis:
            app.state.redis = redis
            app.state.velobank_preview_store = (
                RedisVeloBankPreviewStore(redis)
                if redis is not None
                else VeloBankPreviewStore()
            )
            if snapshot_store is not None:
                app.state.snapshot_store = snapshot_store
            elif redis is not None:
                app.state.snapshot_store = RedisSnapshotStore(
                    redis,
                    key=settings.TRANSACTION_SNAPSHOT_REDIS_KEY,
                    retention_seconds=settings.TRANSACTION_SNAPSHOT_RETENTION_SECONDS,
                )
            else:
                app.state.snapshot_store = InMemorySnapshotStore()
            app.state.matching_backend = (
                RedisMatchBackend(redis) if redis is not None else MemoryMatchBackend()
            )
            app.state.allegro_runtime_state = AllegroStateStore(
                match_store=AllegroMatchStore(app.state.matching_backend)
            )
            app.state.blik_runtime_state = BlikStateStore(
                match_store=BlikMatchStore(app.state.matching_backend)
            )
            app.state.transaction_snapshot_service = None
            app.state.blik_application_runtime = None
            app.state.tx_application_runtime = None
            # Redis expires data itself. Keep the periodic cleanup only for
            # local development, so idle in-memory previews also get discarded.
            preview_store = app.state.velobank_preview_store

            async def purge_memory_previews():
                while True:
                    await asyncio.sleep(60)
                    preview_store.purge_expired()

            cleanup = (
                asyncio.create_task(purge_memory_previews())
                if isinstance(preview_store, VeloBankPreviewStore)
                else None
            )
            try:
                yield
            finally:
                if cleanup is not None:
                    cleanup.cancel()
                    with suppress(asyncio.CancelledError):
                        await cleanup
                app.state.velobank_preview_store = None
                tx_runtime = app.state.tx_application_runtime
                blik_runtime = app.state.blik_application_runtime
                managers = [
                    tx_runtime.tx_metrics_manager if tx_runtime is not None else None,
                    blik_runtime.blik_metrics_manager
                    if blik_runtime is not None
                    else None,
                    app.state.allegro_runtime_state.metrics_manager,
                ]
                for manager in managers:
                    if manager is not None:
                        await manager.aclose()
                snapshot_service = app.state.transaction_snapshot_service
                if snapshot_service is not None:
                    await snapshot_service.aclose()
                app.state.transaction_snapshot_service = None
                app.state.redis = None

    app.router.lifespan_context = lifespan

    register_middlewares(app, settings)

    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(blik_router)
    app.include_router(velobank_import_router)
    app.include_router(transactions_router)
    app.include_router(tx_router)
    app.include_router(allegro_router)
    app.include_router(users_router)
    app.include_router(user_secrets_router)
    app.include_router(system_router)

    return app


# ==================================================
# Production composition root – ONLY HERE engine/db
# ==================================================


def create_production_app() -> FastAPI:
    engine = create_engine_from_url(settings.database_url)
    session_factory = create_session_factory(engine)

    app = create_app(bootstrap=DatabaseBootstrap(engine))
    app.state.session_factory = session_factory

    return app
