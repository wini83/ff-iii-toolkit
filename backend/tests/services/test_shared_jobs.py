"""Durable job contracts, using only synthetic records and disposable Redis."""

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from typing import get_origin
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from services.allegro_state_store import AllegroApplyJobManager
from services.allegro_stats.manager import AllegroMetricsManager
from services.blik_state_store import BlikApplyJobManager
from services.blik_stats.manager import BlikMetricsManager
from services.domain.job_base import JobStatus
from services.domain.metrics import (
    AllegroMetrics,
    BlikStatisticsMetrics,
    TXStatisticsMetrics,
)
from services.infrastructure.redis import redis_connection
from services.jobs.runtime import JobRuntime
from services.jobs.store import (
    INTERRUPTED,
    MAX_RECORD_BYTES,
    JobBusy,
    JobLeaseLost,
    JobStoreUnavailable,
    MemoryJobStore,
    Record,
    RedisJobStore,
)
from services.tx_stats.manager import TxMetricsManager


@asynccontextmanager
async def stores(kind, *, lease=120, retention=86400):
    if kind == "memory":
        store = MemoryJobStore(lease_seconds=lease, retention=retention)
        yield store, store
        return
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run shared jobs integration tests")
    async with (
        redis_connection(SecretStr(url)) as first,
        redis_connection(SecretStr(url)) as second,
    ):
        namespace = f"test:{{jobs-{uuid4().hex}}}"
        try:
            yield (
                RedisJobStore(
                    first, namespace=namespace, lease_seconds=lease, retention=retention
                ),
                RedisJobStore(
                    second,
                    namespace=namespace,
                    lease_seconds=lease,
                    retention=retention,
                ),
            )
        finally:
            keys = [key async for key in first.scan_iter(match=namespace + ":*")]
            if keys:
                await first.delete(*keys)


def record(**kwargs):
    return Record(
        id=str(uuid4()),
        owner="synthetic-owner",
        kind="test",
        scope="test",
        data={"results": [], "amount": "12.340000001"},
        **kwargs,
    )


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_claim_is_atomic_and_terminal_state_is_immutable(kind):
    async def check():
        async with stores(kind) as (first, second):
            results = await asyncio.gather(
                first.claim(record()), second.claim(record())
            )
            assert sum(claimed for _, claimed in results) == 1
            job = next(r for r, claimed in results if claimed)
            assert (await second.get(job.id)).data == job.data
            assert (await second.current(job.scope)).id == job.id
            impostor = job.model_copy(update={"token": "wrong"})
            with pytest.raises(JobLeaseLost):
                await second.save(impostor)
            job.status = "done"
            await first.save(job)
            with pytest.raises(JobLeaseLost):
                await second.save(job)
            assert (await first.get(job.id)).status == "done"
            newer, claimed = await second.claim(record())
            assert claimed and newer.id != job.id
            assert (await first.get(job.id)).status == "done"

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_expired_execution_is_failed_and_never_resumed(kind):
    async def check():
        async with stores(kind, lease=1) as (first, second):
            job, _ = await first.claim(record())
            job.in_flight = 42
            await first.save(job)
            await asyncio.sleep(1.1)
            recovered = await second.get(job.id)
            assert recovered.status == "failed"
            assert recovered.error == INTERRUPTED
            assert recovered.finished is not None
            assert recovered.in_flight == 42
            assert recovered.data["results"] == []
            with pytest.raises(JobLeaseLost):
                await first.heartbeat(job.id, job.token)
            with pytest.raises(JobLeaseLost):
                await first.save(job)
            assert (await second.claim(record()))[1]

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_completed_job_ttl_is_fixed_despite_polling(kind):
    async def check():
        async with stores(kind, retention=1) as (first, second):
            job, _ = await first.claim(record())
            job.status = "done"
            await first.save(job)
            await asyncio.sleep(0.6)
            assert await second.get(job.id)
            await asyncio.sleep(0.6)
            assert await first.get(job.id) is None

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_heartbeat_prevents_false_interruption(kind):
    async def check():
        async with stores(kind, lease=2) as (first, second):
            runtime = JobRuntime(first)
            job, _ = await first.claim(record())
            release = asyncio.Event()

            async def body():
                await release.wait()
                job.status = "done"
                await first.save(job)

            runtime.spawn(job, body())
            await asyncio.sleep(2.1)
            assert (await second.get(job.id)).status == "pending"
            release.set()
            await asyncio.gather(*runtime.tasks)
            assert (await second.get(job.id)).status == "done"
            await runtime.aclose()

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
@pytest.mark.parametrize("feature", ["blik", "allegro"])
def test_apply_polling_survives_new_manager_and_enforces_owner(kind, feature):
    async def check():
        async with stores(kind) as (first, second):
            manager_type = (
                BlikApplyJobManager if feature == "blik" else AllegroApplyJobManager
            )
            owner = uuid4()
            writer = manager_type(JobRuntime(first))
            reader = manager_type(JobRuntime(second))
            resource = (
                {"file_id": "synthetic"}
                if feature == "blik"
                else {"secret_id": uuid4()}
            )
            job = await writer.create(owner=owner, total=2, **resource)
            assert await reader.get(job.id, owner=uuid4()) is None
            assert await reader.get(job.id, owner=owner) == job
            with pytest.raises(JobBusy):
                await reader.create(owner=owner, total=2, **resource)
            job.status = JobStatus.RUNNING
            await writer.save(job, in_flight=42)
            assert (await reader.get(job.id, owner=owner)).status == JobStatus.RUNNING
            job.status = JobStatus.DONE
            await writer.save(job)
            recovered = await reader.get(job.id, owner=owner)
            assert recovered.status == JobStatus.DONE
            assert recovered.finished_at is not None

    asyncio.run(check())


CASES = [
    (TxMetricsManager, TXStatisticsMetrics),
    (BlikMetricsManager, BlikStatisticsMetrics),
    (AllegroMetricsManager, AllegroMetrics),
]


def metrics(model, stamp):
    values = {
        f.name: (
            stamp if f.name == "time_stamp" else {} if get_origin(f.type) is dict else 1
        )
        for f in fields(model)
    }
    if model is TXStatisticsMetrics:
        values["single_part_amount"] = {"PLN": Decimal("12.340000001")}
    return model(**values)


async def wait_terminal(manager):
    async with asyncio.timeout(3):
        while True:
            state = await manager.get_state()
            if state.status in {JobStatus.DONE, JobStatus.FAILED}:
                return state
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("kind", ["memory", "redis"])
@pytest.mark.parametrize("manager_type,model", CASES)
def test_metrics_single_execution_freshness_and_cross_worker_results(
    kind, manager_type, model
):
    async def check():
        async with stores(kind) as (first, second):
            stamp = datetime.now(UTC)
            result = metrics(model, stamp)
            release = asyncio.Event()

            async def compute():
                await release.wait()
                return result

            provider = MagicMock()
            provider.fetch_metrics = AsyncMock(side_effect=compute)
            provider.refresh_metrics = AsyncMock(side_effect=compute)
            provider.get_cached_snapshot_timestamp = AsyncMock(return_value=stamp)
            one = manager_type(provider=provider, runtime=JobRuntime(first))
            two = manager_type(provider=provider, runtime=JobRuntime(second))
            states = await asyncio.gather(one.ensure_current(), two.ensure_current())
            assert all(s.status == JobStatus.RUNNING for s in states)
            await asyncio.sleep(0.05)
            assert provider.fetch_metrics.await_count == 1
            release.set()
            state = await wait_terminal(two)
            assert state.result == result
            assert state.error is None
            assert (await two.ensure_current()).result == result
            assert provider.fetch_metrics.await_count == 1
            await two.refresh()
            assert (await wait_terminal(one)).result == result
            assert provider.refresh_metrics.await_count == 1
            await one.aclose()
            await two.aclose()

    asyncio.run(check())


@pytest.mark.parametrize("manager_type,model", CASES)
def test_metrics_failure_and_shutdown_are_explicit_and_retry_is_manual(
    manager_type, model
):
    async def check():
        provider = MagicMock()
        provider.refresh_metrics = AsyncMock(
            side_effect=RuntimeError("synthetic private data")
        )
        store = MemoryJobStore()
        manager = manager_type(provider=provider, runtime=JobRuntime(store))
        await manager.refresh()
        state = await wait_terminal(manager)
        assert state.status == JobStatus.FAILED
        assert "private" not in state.error
        assert (await manager.ensure_current()).status == JobStatus.FAILED
        assert provider.refresh_metrics.await_count == 1
        entered = asyncio.Event()

        async def hold():
            entered.set()
            await asyncio.Event().wait()

        provider.refresh_metrics = AsyncMock(side_effect=hold)
        await manager.refresh()
        await entered.wait()
        await manager.aclose()
        assert (await manager.get_state()).status == JobStatus.FAILED
        assert "interrupted" in (await manager.get_state()).error
        assert not manager.runtime.tasks

    asyncio.run(check())


def test_job_payload_bound_and_errors_are_sanitized():
    async def check():
        store = MemoryJobStore()
        huge = record()
        huge.data = {"x": "x" * MAX_RECORD_BYTES}
        with pytest.raises(JobStoreUnavailable):
            await store.claim(huge)
        redis = MagicMock()
        redis.eval = AsyncMock(side_effect=OSError("private connection data"))
        with pytest.raises(JobStoreUnavailable) as exc:
            await RedisJobStore(redis).claim(record())
        assert "private" not in str(exc.value)

    asyncio.run(check())


@pytest.mark.parametrize("feature", ["blik", "allegro"])
@pytest.mark.parametrize("failure_at", ["before", "after"])
def test_apply_storage_failure_stops_further_financial_operations(feature, failure_at):
    from api.mappers.blik import build_blik_match_id
    from services.allegro_application_service import AllegroApplicationService
    from services.allegro_state_store import AllegroStateStore
    from services.blik_application_service import BlikApplicationService
    from services.blik_state_store import BlikStateStore
    from services.domain.allegro import MatchDecision as AllegroDecision
    from services.domain.blik import MatchDecision as BlikDecision
    from tests.services.test_matching_cache import blik_matches

    async def check():
        manager_type = (
            BlikApplyJobManager if feature == "blik" else AllegroApplyJobManager
        )
        backend = MemoryJobStore()
        manager = manager_type(JobRuntime(backend))
        resource = (
            {"file_id": "synthetic"} if feature == "blik" else {"secret_id": uuid4()}
        )
        job = await manager.create(owner=uuid4(), total=2, **resource)
        service_type = (
            BlikApplicationService if feature == "blik" else AllegroApplicationService
        )
        service = object.__new__(service_type)
        service.state_store = (
            BlikStateStore(job_manager=manager)
            if feature == "blik"
            else AllegroStateStore(job_manager=manager)
        )
        service.enrichment_service = MagicMock()
        match = blik_matches()[0]
        if feature == "blik":
            decision = BlikDecision(
                transaction_id=match.tx.id,
                selected_match_id=build_blik_match_id(
                    transaction_id=match.tx.id, record=match.matches[0]
                ),
            )
        else:
            from tests.services.test_allegro_application_service import _payment

            match.matches = [_payment("synthetic", "full")]
            decision = AllegroDecision(
                transaction_id=match.tx.id, payment_id="synthetic"
            )
        original_save = backend.save

        async def save(record):
            if failure_at == "before" and record.in_flight is not None:
                raise JobStoreUnavailable("synthetic outage")
            if failure_at == "after" and record.data["results"]:
                raise JobStoreUnavailable("synthetic outage")
            return await original_save(record)

        backend.save = save
        service.enrichment_service.apply_match = AsyncMock()
        with pytest.raises(JobStoreUnavailable):
            await service._run_apply_job(
                job=job, decisions=[decision, decision], matches=[match]
            )
        assert service.enrichment_service.apply_match.await_count == (
            1 if failure_at == "after" else 0
        )
        assert not manager.executions

    asyncio.run(check())


def test_runtime_shutdown_before_task_starts_closes_work_and_fails_pending_job():
    async def check():
        store = MemoryJobStore()
        runtime = JobRuntime(store)
        job, _ = await store.claim(record())
        work = AsyncMock()
        coroutine = work()
        runtime.spawn(job, coroutine)
        await runtime.aclose()
        work.assert_not_awaited()
        assert coroutine.cr_frame is None
        assert (await store.get(job.id)).status == "failed"

    asyncio.run(check())


def test_real_process_exit_preserves_progress_without_replaying_apply():
    import subprocess
    import sys

    async def check():
        async with stores("redis", lease=1) as (first, second):
            owner = uuid4()
            script = r"""
import asyncio, os
from uuid import UUID
from pydantic import SecretStr
from services.infrastructure.redis import redis_connection
from services.jobs.store import RedisJobStore
from services.jobs.runtime import JobRuntime
from services.blik_state_store import BlikApplyJobManager
from services.domain.job_base import JobStatus
from services.domain.blik import ApplyOutcome
async def main():
    async with redis_connection(SecretStr(os.environ["TEST_REDIS_URL"])) as redis:
        store = RedisJobStore(redis, namespace=os.environ["JOB_NAMESPACE"], lease_seconds=1)
        manager = BlikApplyJobManager(JobRuntime(store))
        job = await manager.create(owner=UUID(os.environ["JOB_OWNER"]), file_id="synthetic", total=2)
        job.status = JobStatus.RUNNING
        job.applied = 1
        job.results = [ApplyOutcome(transaction_id=1, selected_match_id="synthetic", status="success")]
        await manager.save(job, in_flight=2)
        print(job.id)
asyncio.run(main())
"""
            from pathlib import Path

            env = dict(
                os.environ,
                JOB_NAMESPACE=first.namespace,
                JOB_OWNER=str(owner),
                PYTHONPATH=str(Path("src").resolve()),
            )
            # A new OS process writes its last checkpoint and disappears without completing.
            completed = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            from uuid import UUID

            job_id = UUID(completed.stdout.strip())
            await asyncio.sleep(1.1)
            reader = BlikApplyJobManager(JobRuntime(second))
            job = await reader.get(job_id, owner=owner)
            assert job.status == JobStatus.FAILED
            assert job.applied == 1
            assert job.results[0].transaction_id == 1
            assert job.results[0].status == "success"
            assert "transaction 2 may have been updated" in job.results[-1].reason
            assert not reader.runtime.tasks
            assert (await reader.get(job_id, owner=owner)).results == job.results

    asyncio.run(check())


@pytest.mark.parametrize("feature", ["blik", "allegro"])
def test_api_polling_after_application_restart_uses_persisted_owner(
    monkeypatch, feature
):
    from functools import partial

    from fastapi.testclient import TestClient

    from api.deps_runtime import (
        get_allegro_application_runtime,
        get_blik_application_runtime,
    )
    from main import create_app
    from services.allegro_application_service import AllegroApplicationService
    from services.blik_application_service import BlikApplicationService
    from services.guards import require_active_user
    from settings import settings

    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for application-restart polling test")
    monkeypatch.setattr(settings, "REDIS_URL", SecretStr(url))
    owner = uuid4()
    dependency = (
        get_blik_application_runtime
        if feature == "blik"
        else get_allegro_application_runtime
    )
    service_type = (
        BlikApplicationService if feature == "blik" else AllegroApplicationService
    )
    for mode in ["write", "read"]:
        app = create_app()
        app.dependency_overrides[require_active_user] = lambda: owner
        service = object.__new__(service_type)

        def bind(current_service):
            return lambda: current_service

        app.dependency_overrides[dependency] = bind(service)
        with TestClient(app) as client:
            service.state_store = (
                app.state.blik_runtime_state
                if feature == "blik"
                else app.state.allegro_runtime_state
            )
            manager = service.state_store.job_manager
            assert manager.runtime is app.state.job_runtime
            assert manager.runtime.store.redis is app.state.redis
            if mode == "write":
                resource = (
                    {"file_id": "synthetic-no-longer-exists"}
                    if feature == "blik"
                    else {"secret_id": uuid4()}
                )
                job = client.portal.call(
                    partial(manager.create, owner=owner, total=0, **resource)
                )
                job.status = JobStatus.DONE
                client.portal.call(partial(manager.save, job))
                job_id = job.id
            else:
                prefix = "blik_files" if feature == "blik" else "allegro"
                response = client.get(f"/api/{prefix}/apply-jobs/{job_id}")
                assert response.status_code == 200
                assert response.json()["status"] == "done"
                assert response.json()["results"] == []
                app.dependency_overrides[require_active_user] = lambda: uuid4()
                assert (
                    client.get(f"/api/{prefix}/apply-jobs/{job_id}").status_code == 404
                )


def test_real_redis_oom_rejects_job_admission_and_progress_before_mutation():
    from redis.exceptions import OutOfMemoryError

    async def check():
        url = os.getenv("TEST_REDIS_OOM_URL")
        if not url:
            pytest.skip("Set isolated TEST_REDIS_OOM_URL for destructive OOM test")
        async with redis_connection(SecretStr(url)) as redis:
            store = RedisJobStore(redis, namespace=f"test:{{jobs-oom-{uuid4().hex}}}")
            job, _ = await store.claim(record())
            previous = await redis.get(store.prefix + job.id)
            config = await redis.config_get("maxmemory", "maxmemory-policy")
            try:
                await redis.config_set("maxmemory-policy", "noeviction")
                await redis.config_set("maxmemory", 1)
                with pytest.raises(OutOfMemoryError):
                    await redis.set(store.namespace + ":probe", "must fail")
                job.status = "done"
                with pytest.raises(JobStoreUnavailable):
                    await store.save(job)
                with pytest.raises(JobStoreUnavailable):
                    await store.claim(record())
                assert await redis.get(store.prefix + job.id) == previous
            finally:
                await redis.config_set("maxmemory", config["maxmemory"])
                await redis.config_set("maxmemory-policy", config["maxmemory-policy"])
                keys = [
                    key async for key in redis.scan_iter(match=store.namespace + ":*")
                ]
                if keys:
                    await redis.delete(*keys)

    asyncio.run(check())


def test_heartbeat_failure_cancels_execution_and_persists_interruption():
    async def check():
        store = MemoryJobStore(lease_seconds=0.15)
        runtime = JobRuntime(store)
        job, _ = await store.claim(record())
        canceled = asyncio.Event()

        async def work():
            try:
                await asyncio.Event().wait()
            finally:
                canceled.set()

        store.heartbeat = AsyncMock(side_effect=JobStoreUnavailable("synthetic outage"))
        runtime.spawn(job, work())
        async with asyncio.timeout(2):
            await asyncio.gather(*runtime.tasks)
        assert canceled.is_set()
        assert (await store.get(job.id)).status == "failed"
        assert "interrupted" in (await store.get(job.id)).error
        assert not runtime.tasks

    asyncio.run(check())


def test_redis_completion_preserves_subsecond_server_time():
    async def check():
        async with stores("redis") as (store, reader_store):
            original_eval = store.redis.eval

            async def frozen_server_time(script, *args):
                # Freeze Redis TIME for deterministic same-second completion.
                # Real Lua still computes the deadline and final timestamp.
                script = script.replace(
                    "redis.call('TIME')", "({'1800000000', '875123'})"
                )
                return await original_eval(script, *args)

            store.redis.eval = frozen_server_time
            owner = uuid4()
            writer = BlikApplyJobManager(JobRuntime(store))
            job = await writer.create(owner=owner, total=0, file_id="synthetic")
            job.started_at = datetime.fromtimestamp(1800000000.75, UTC)
            job.status = JobStatus.DONE
            await writer.save(job)
            reader = BlikApplyJobManager(JobRuntime(reader_store))
            recovered = await reader.get(job.id, owner=owner)
            assert recovered.finished_at == datetime.fromtimestamp(
                1800000000.875123, UTC
            )
            assert recovered.finished_at >= recovered.started_at
            assert (
                recovered.finished_at - recovered.started_at
            ).total_seconds() == 0.125123

    asyncio.run(check())
