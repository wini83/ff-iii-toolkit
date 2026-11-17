"""One metrics lifecycle shared by TX, BLIK and Allegro."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import TypeAdapter

from services.domain.job_base import JobStatus
from services.domain.metrics import BaseMetrics
from services.jobs.runtime import JobRuntime, fail_record
from services.jobs.store import JobStoreUnavailable, MemoryJobStore, Record
from services.tx_stats.models import MetricsState
from services.tx_stats.runner import MetricsProvider


class MetricsManager[T: BaseMetrics]:
    def __init__(
        self,
        *,
        provider: MetricsProvider[T],
        model: type[T],
        feature: str,
        runtime: JobRuntime | None = None,
    ):
        self._provider = provider
        self.runtime = runtime if runtime is not None else JobRuntime(MemoryJobStore())
        self.scope = "metrics:" + feature
        self.adapter = TypeAdapter(MetricsState[model])

    def _state(self, record: Record | None) -> MetricsState[T]:
        if record is None:
            return MetricsState(
                status=JobStatus.PENDING,
                result=None,
                error=None,
                progress=None,
                last_updated_at=None,
            )
        try:
            state = self.adapter.validate_python(record.data)
        except (ValueError, TypeError):
            raise JobStoreUnavailable("Invalid metrics state") from None
        state.status = JobStatus(record.status)
        if record.error:
            state.error, state.result, state.progress = record.error, None, None
        return state

    async def get_state(self) -> MetricsState[T]:
        return self._state(await self.runtime.store.current(self.scope))

    async def ensure_current(self) -> MetricsState[T]:
        record = await self.runtime.store.current(self.scope)
        state = self._state(record)
        if state.status in {JobStatus.RUNNING, JobStatus.FAILED}:
            return state
        timestamp = await self._provider.get_cached_snapshot_timestamp()
        if (
            state.status == JobStatus.DONE
            and state.result is not None
            and timestamp is not None
            and state.result.time_stamp == timestamp
        ):
            return state
        return await self._start(force_refresh=False)

    async def refresh(self) -> MetricsState[T]:
        return await self._start(force_refresh=True)

    async def _start(self, *, force_refresh: bool) -> MetricsState[T]:
        state = MetricsState(
            status=JobStatus.RUNNING,
            result=None,
            error=None,
            progress="queued",
            last_updated_at=None,
        )
        record, claimed = await self.runtime.store.claim(
            Record(
                id=str(uuid4()),
                owner="ledger",
                kind=self.scope,
                scope=self.scope,
                status="running",
                data=self.adapter.dump_python(state, mode="json"),
            )
        )
        if claimed:
            self.runtime.spawn(record, self._compute(record, force_refresh))
        return self._state(record)

    async def _compute(self, record: Record, force_refresh: bool):
        state = self._state(record)
        try:
            state.progress = "fetching"
            record.data = self.adapter.dump_python(state, mode="json")
            await self.runtime.store.save(record)
            state.result = await (
                self._provider.refresh_metrics()
                if force_refresh
                else self._provider.fetch_metrics()
            )
            state.status, state.progress = JobStatus.DONE, None
            state.last_updated_at = datetime.now(UTC)
            record.status = "done"
            record.data = self.adapter.dump_python(state, mode="json")
            await self.runtime.store.save(record)
        except asyncio.CancelledError:
            await fail_record(
                self.runtime.store,
                record,
                "Metrics computation interrupted; refresh to retry",
            )
            raise
        except Exception:
            await fail_record(
                self.runtime.store,
                record,
                "Metrics computation failed; refresh to retry",
            )

    async def aclose(self):
        await self.runtime.aclose()
