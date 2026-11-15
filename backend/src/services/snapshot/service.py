import asyncio
import logging
from datetime import UTC, datetime

from services.firefly_base_service import FireflyBaseService
from services.snapshot.coordination import (
    SnapshotRefreshCoordinator,
    SnapshotRefreshTimeout,
)
from services.snapshot.models import TransactionSnapshot
from services.snapshot.store import SnapshotStore, snapshot_is_stale

logger = logging.getLogger(__name__)
SNAPSHOT_FETCH_TIMEOUT_SECONDS = 180


class TransactionSnapshotService:
    def __init__(
        self,
        store: SnapshotStore,
        firefly_service: FireflyBaseService,
        max_age_seconds: int = 300,
        coordinator: SnapshotRefreshCoordinator | None = None,
    ) -> None:
        self.store = store
        self.firefly_service = firefly_service
        self.max_age_seconds = max_age_seconds
        self.coordinator = coordinator
        self._refresh_baseline: str | None = None
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[TransactionSnapshot] | None = None

    async def get_snapshot(self) -> TransactionSnapshot:
        snapshot = await self.store.get_snapshot()
        if snapshot is not None and not snapshot_is_stale(
            snapshot, self.max_age_seconds
        ):
            return snapshot
        return await self._ensure_snapshot(force_refresh=False)

    async def refresh_snapshot(self) -> TransactionSnapshot:
        return await self._ensure_snapshot(force_refresh=True)

    async def get_cached_snapshot(self) -> TransactionSnapshot | None:
        return await self.store.get_snapshot()

    async def get_cached_snapshot_timestamp(self) -> datetime | None:
        snapshot = await self.store.get_snapshot()
        if snapshot is None:
            return None
        if snapshot_is_stale(snapshot, self.max_age_seconds):
            return None
        return snapshot.fetched_at

    async def _ensure_snapshot(self, *, force_refresh: bool) -> TransactionSnapshot:
        if self.coordinator is None:
            return await self._ensure_local_snapshot(force_refresh=force_refresh)
        try:
            # Includes baseline reads and waiting behind an older local task.
            async with asyncio.timeout(self.coordinator.wait_timeout_seconds):
                return await self._ensure_local_snapshot(force_refresh=force_refresh)
        except TimeoutError:
            raise SnapshotRefreshTimeout(
                "Transaction snapshot refresh timed out"
            ) from None

    async def _ensure_local_snapshot(
        self, *, force_refresh: bool
    ) -> TransactionSnapshot:
        baseline = None
        if self.coordinator is not None:
            state = await self.coordinator.read_state()
            baseline = state.revision
            if (
                not force_refresh
                and state.snapshot is not None
                and not snapshot_is_stale(state.snapshot, self.max_age_seconds)
            ):
                return state.snapshot

        while True:
            async with self._refresh_lock:
                if self.coordinator is None and not force_refresh:
                    snapshot = await self.store.get_snapshot()
                    if snapshot is not None and not snapshot_is_stale(
                        snapshot, self.max_age_seconds
                    ):
                        return snapshot
                task = self._refresh_task
                if task is None or task.done():
                    task = asyncio.create_task(
                        self._run_refresh(
                            force_refresh=force_refresh, baseline=baseline
                        ),
                        name="transaction-snapshot-refresh",
                    )
                    task.add_done_callback(self._log_refresh_task_failure)
                    self._refresh_task = task
                    self._refresh_baseline = baseline
                # A force request observed a publication newer than this local
                # task's baseline. Wait for it, then re-evaluate the pinned revision.
                join = not force_refresh or self._refresh_baseline == baseline
            try:
                result = await asyncio.shield(task)
                if join:
                    return result
            finally:
                if task.done() and self._refresh_task is task:
                    self._refresh_task = None

    async def _run_refresh(
        self, *, force_refresh: bool, baseline: str | None
    ) -> TransactionSnapshot:
        if self.coordinator is not None:
            return await self.coordinator.refresh(
                self._fetch_snapshot,
                force_refresh=force_refresh,
                baseline_revision=baseline,
                max_age_seconds=self.max_age_seconds,
            )
        return await self._fetch_and_store_snapshot()

    async def _fetch_snapshot(self) -> TransactionSnapshot:
        transactions, metrics = await asyncio.wait_for(
            self.firefly_service.fetch_transactions_with_metrics(),
            timeout=SNAPSHOT_FETCH_TIMEOUT_SECONDS,
        )
        return TransactionSnapshot(
            transactions=transactions,
            metrics=metrics,
            fetched_at=datetime.now(UTC),
        )

    async def _fetch_and_store_snapshot(self) -> TransactionSnapshot:
        snapshot = await self._fetch_snapshot()
        await self.store.set_snapshot(snapshot)
        return snapshot

    async def aclose(self) -> None:
        task = self._refresh_task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._refresh_task = None

    def _log_refresh_task_failure(
        self, task: asyncio.Task[TransactionSnapshot]
    ) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            logger.warning("Transaction snapshot refresh task was cancelled")
        except Exception:
            logger.exception("Transaction snapshot refresh task failed")
