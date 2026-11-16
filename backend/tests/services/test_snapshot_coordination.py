import asyncio
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError

from services.domain.metrics import FetchMetrics
from services.infrastructure.redis import redis_connection
from services.snapshot.coordination import (
    RedisRefreshCoordinator,
    SnapshotRefreshTimeout,
)
from services.snapshot.models import TransactionSnapshot
from services.snapshot.redis_store import RedisSnapshotStore, SnapshotStoreUnavailable
from services.snapshot.service import TransactionSnapshotService


def snapshot(marker=0, *, stale=False):
    return TransactionSnapshot(
        [],
        FetchMetrics(0, marker, 0, 0),
        datetime.now(UTC) - timedelta(seconds=301 if stale else 0),
    )


@asynccontextmanager
async def case():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for Redis coordination tests")
    async with redis_connection(SecretStr(url)) as redis:
        store = RedisSnapshotStore(
            redis, key=f"toolkit:test:coord:{uuid4().hex}", retention_seconds=60
        )
        coordinator = RedisRefreshCoordinator(
            store,
            lease_seconds=0.5,
            wait_timeout_seconds=3,
            poll_seconds=(0.003, 0.006),
        )
        try:
            yield redis, store, coordinator
        finally:
            await redis.delete(
                store.key, coordinator.lock_key, coordinator.revision_key
            )


def service(store, coordinator, fetch):
    firefly = MagicMock()
    firefly.fetch_transactions_with_metrics = AsyncMock(side_effect=fetch)
    return TransactionSnapshotService(store, firefly, coordinator=coordinator)


@pytest.mark.parametrize("force", [False, True])
def test_independent_instances_share_one_refresh(force):
    async def check():
        async with case() as (redis, store, seed):
            token = uuid4().hex
            assert await seed.acquire(token)
            assert await seed.publish(token, snapshot(stale=not force))
            all_ready = asyncio.Event()
            ready = 0
            fetches = 0
            services = []
            for _ in range(6):
                coord = RedisRefreshCoordinator(
                    store,
                    lease_seconds=1,
                    wait_timeout_seconds=3,
                    poll_seconds=(0.003, 0.006),
                )

                def track(coordinator):
                    original = coordinator.read_state
                    first = True

                    async def read():
                        nonlocal ready, first
                        result = await original()
                        if first:
                            first = False
                            ready += 1
                            if ready == 6:
                                all_ready.set()
                        return result

                    coordinator.read_state = read

                track(coord)

                async def fetch():
                    nonlocal fetches
                    fetches += 1
                    await all_ready.wait()
                    return [], FetchMetrics(0, 1, 0, 0)

                services.append(service(store, coord, fetch))
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        s.refresh_snapshot() if force else s.get_snapshot()
                        for s in services
                    ]
                ),
                4,
            )
            assert fetches == 1
            assert all(result == results[0] for result in results)
            assert await store.get_snapshot() == results[0]
            assert await redis.get(services[0].coordinator.lock_key) is None

    asyncio.run(check())


def test_fresh_get_does_not_acquire_a_lock_or_fetch():
    async def check():
        async with case() as (_, store, coordinator):
            value = snapshot()
            await store.set_snapshot(value)
            coordinator.acquire = AsyncMock(
                side_effect=AssertionError("Unexpected lock")
            )
            fetch = AsyncMock(side_effect=AssertionError("Unexpected fetch"))
            assert await service(store, coordinator, fetch).get_snapshot() == value

    asyncio.run(check())


def test_force_uses_revision_even_when_snapshot_and_timestamp_do_not_change():
    async def check():
        async with case() as (_, store, coordinator):
            value = snapshot()
            fetch = AsyncMock(return_value=value)
            await store.set_snapshot(value)  # v1 payload from #108, without a revision.
            for _ in range(2):
                before = await coordinator.read_state()
                assert (
                    await coordinator.refresh(
                        fetch,
                        force_refresh=True,
                        baseline_revision=before.revision,
                        max_age_seconds=300,
                    )
                    == value
                )
                after = await coordinator.read_state()
                assert after.revision is not None and after.revision != before.revision
                assert after.snapshot == value
            assert fetch.await_count == 2

    asyncio.run(check())


def test_expired_owner_cannot_publish_or_release_successors_lock():
    async def check():
        async with case() as (redis, store, coordinator):
            owner, successor = uuid4().hex, uuid4().hex
            assert await coordinator.acquire(owner)
            await redis.pexpire(coordinator.lock_key, 1)
            await asyncio.sleep(0.02)
            assert await coordinator.acquire(successor)
            assert not await coordinator.publish(owner, snapshot(1))
            await coordinator.release(owner)
            assert await redis.get(coordinator.lock_key) == successor
            value = snapshot(2)
            assert await coordinator.publish(successor, value)
            assert not await coordinator.publish(owner, snapshot(1))
            assert (await coordinator.read_state()).revision == successor
            assert await store.get_snapshot() == value

    asyncio.run(check())


def test_active_fetch_losing_lease_returns_winner_not_its_own_candidate():
    async def check():
        async with case() as (redis, store, coordinator):
            started, finish_old = asyncio.Event(), asyncio.Event()

            async def old_fetch():
                started.set()
                await finish_old.wait()
                return [], FetchMetrics(0, 111, 0, 0)

            old = service(store, coordinator, old_fetch)
            task = asyncio.create_task(old.get_snapshot())
            await started.wait()
            await redis.pexpire(coordinator.lock_key, 1)
            await asyncio.sleep(0.02)
            newer = RedisRefreshCoordinator(
                store,
                lease_seconds=1,
                wait_timeout_seconds=3,
                poll_seconds=(0.003, 0.006),
            )
            winner = await service(
                store, newer, AsyncMock(return_value=([], FetchMetrics(0, 222, 0, 0)))
            ).get_snapshot()
            finish_old.set()
            assert await asyncio.wait_for(task, 2) == winner
            assert winner.metrics.fetching_duration_ms == 222
            assert await store.get_snapshot() == winner

    asyncio.run(check())


def test_abandoned_lease_expires_and_next_request_recovers():
    async def check():
        async with case() as (redis, store, coordinator):
            assert await coordinator.acquire(uuid4().hex)
            await redis.pexpire(coordinator.lock_key, 20)
            fetch = AsyncMock(return_value=([], FetchMetrics(0, 1, 0, 0)))
            result = await service(store, coordinator, fetch).get_snapshot()
            assert result == await store.get_snapshot()
            fetch.assert_awaited_once()

    asyncio.run(check())


def test_fetch_failure_releases_lock_and_preserves_previous_snapshot():
    async def check():
        async with case() as (redis, store, coordinator):
            old = snapshot(stale=True)
            await store.set_snapshot(old)
            with pytest.raises(RuntimeError, match="Firefly failed"):
                await service(
                    store,
                    coordinator,
                    AsyncMock(side_effect=RuntimeError("Firefly failed")),
                ).get_snapshot()
            assert await redis.get(coordinator.lock_key) is None
            assert await store.get_snapshot() == old
            value = await service(
                store,
                coordinator,
                AsyncMock(return_value=([], FetchMetrics(0, 1, 0, 0))),
            ).get_snapshot()
            assert await store.get_snapshot() == value

    asyncio.run(check())


def test_fetch_timeout_releases_lock(monkeypatch):
    monkeypatch.setattr(
        "services.snapshot.service.SNAPSHOT_FETCH_TIMEOUT_SECONDS", 0.02
    )

    async def check():
        async with case() as (redis, store, coordinator):

            async def stall():
                await asyncio.Event().wait()

            with pytest.raises(SnapshotRefreshTimeout):
                await service(store, coordinator, stall).get_snapshot()
            assert await redis.get(coordinator.lock_key) is None
            assert await store.get_snapshot() is None

    asyncio.run(check())


def test_wait_timeout_does_not_remove_other_owners_lock():
    async def check():
        async with case() as (redis, store, coordinator):
            token = uuid4().hex
            assert await coordinator.acquire(token)
            coordinator.wait_timeout_seconds = 0.03
            worker = service(
                store,
                coordinator,
                AsyncMock(side_effect=AssertionError("Unexpected fetch")),
            )
            try:
                with pytest.raises(SnapshotRefreshTimeout):
                    await asyncio.wait_for(worker.get_snapshot(), 1)
            finally:
                await worker.aclose()
            assert await redis.get(coordinator.lock_key) == token

    asyncio.run(check())


def test_cancelling_one_local_waiter_does_not_cancel_shared_refresh():
    async def check():
        async with case() as (_, store, coordinator):
            started, finish = asyncio.Event(), asyncio.Event()

            async def fetch():
                started.set()
                await finish.wait()
                return [], FetchMetrics(0, 1, 0, 0)

            worker = service(store, coordinator, fetch)
            first = asyncio.create_task(worker.get_snapshot())
            await started.wait()
            second = asyncio.create_task(worker.get_snapshot())
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            finish.set()
            result = await asyncio.wait_for(second, 1)
            assert await store.get_snapshot() == result
            worker.firefly_service.fetch_transactions_with_metrics.assert_awaited_once()

    asyncio.run(check())


@pytest.mark.parametrize(
    "method", ["read_state", "read_revision", "acquire", "publish", "release"]
)
def test_redis_errors_are_explicit_sanitized_and_bounded(method):
    redis = AsyncMock()
    for command in [redis.execute_command, redis.set, redis.eval]:
        command.side_effect = ConnectionError("redis://private:secret@host")
    coordinator = RedisRefreshCoordinator(RedisSnapshotStore(redis))

    async def check():
        with pytest.raises(SnapshotStoreUnavailable) as exc:
            if method == "acquire":
                await coordinator.acquire(uuid4().hex)
            elif method == "publish":
                await coordinator.publish(uuid4().hex, snapshot())
            elif method == "release":
                await coordinator.release(uuid4().hex)
            else:
                await getattr(coordinator, method)()
        assert "secret" not in str(exc.value)

    asyncio.run(check())


def test_new_local_force_after_publication_waits_for_another_revision():
    async def check():
        async with case() as (_, store, coordinator):
            published, finish = asyncio.Event(), asyncio.Event()
            original_publish = coordinator.publish
            publication_count = 0

            async def delayed_publish(token, value):
                nonlocal publication_count
                result = await original_publish(token, value)
                publication_count += 1
                if publication_count == 1:
                    published.set()
                    await finish.wait()
                return result

            coordinator.publish = delayed_publish
            fetch = AsyncMock(return_value=([], FetchMetrics(0, 1, 0, 0)))
            worker = service(store, coordinator, fetch)
            first = asyncio.create_task(worker.refresh_snapshot())
            await published.wait()
            first_revision = await coordinator.read_revision()
            second = asyncio.create_task(worker.refresh_snapshot())
            await asyncio.sleep(0.02)
            assert not second.done()
            finish.set()
            await asyncio.wait_for(asyncio.gather(first, second), 2)
            assert await coordinator.read_revision() != first_revision
            assert fetch.await_count == 2

    asyncio.run(check())


def test_request_deadline_includes_waiting_for_an_older_local_task():
    async def check():
        async with case() as (_, store, coordinator):
            published, finish = asyncio.Event(), asyncio.Event()
            original_publish = coordinator.publish

            async def delayed_publish(token, value):
                result = await original_publish(token, value)
                published.set()
                await finish.wait()
                return result

            coordinator.publish = delayed_publish
            worker = service(
                store,
                coordinator,
                AsyncMock(return_value=([], FetchMetrics(0, 1, 0, 0))),
            )
            first = asyncio.create_task(worker.refresh_snapshot())
            await published.wait()
            coordinator.wait_timeout_seconds = 0.03
            with pytest.raises(SnapshotRefreshTimeout):
                await asyncio.wait_for(worker.refresh_snapshot(), 1)
            assert not first.done()
            finish.set()
            await first

    asyncio.run(check())


@pytest.mark.parametrize("retention", [1.5, 2**63])
def test_invalid_retention_is_rejected_before_any_publication(retention):
    async def check():
        async with case() as (_, store, coordinator):
            old = snapshot(1)
            await store.set_snapshot(old)
            token = uuid4().hex
            assert await coordinator.acquire(token)
            store.retention_seconds = retention
            with pytest.raises(SnapshotStoreUnavailable):
                await coordinator.publish(token, snapshot(2))
            assert await store.get_snapshot() == old
            assert await coordinator.read_revision() is None
            await coordinator.release(token)

    asyncio.run(check())


def test_publication_normalizes_valid_integer_retention_before_expiry_commands():
    async def check():
        async with case() as (redis, store, coordinator):
            token = uuid4().hex
            assert await coordinator.acquire(token)
            # Redis EXPIRE rejects a raw "60.0" argument. The Lua script must
            # use the validated integer rather than the original representation.
            store.retention_seconds = 60.0
            value = snapshot()
            assert await coordinator.publish(token, value)
            assert await store.get_snapshot() == value
            assert 0 < await redis.ttl(store.key) <= 60
            assert 0 < await redis.ttl(coordinator.revision_key) <= 60

    asyncio.run(check())


def test_new_force_recovers_when_older_publication_response_is_lost():
    async def check():
        async with case() as (_, store, coordinator):
            published, fail_response, baseline_seen = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )
            original_publish = coordinator.publish
            original_read = coordinator.read_state
            publications = 0
            fetches = 0

            async def fetch():
                nonlocal fetches
                fetches += 1
                return [], FetchMetrics(0, fetches, 0, 0)

            async def lost_response(token, value):
                nonlocal publications
                result = await original_publish(token, value)
                publications += 1
                if publications == 1:
                    published.set()
                    await fail_response.wait()
                    raise SnapshotStoreUnavailable("Lost publication response")
                return result

            async def observed_read():
                state = await original_read()
                if published.is_set() and state.revision is not None:
                    baseline_seen.set()
                return state

            coordinator.publish = lost_response
            coordinator.read_state = observed_read
            worker = service(store, coordinator, fetch)
            first = asyncio.create_task(worker.refresh_snapshot())
            try:
                await asyncio.wait_for(published.wait(), 1)
                first_revision = await coordinator.read_revision()
                newer = asyncio.create_task(worker.refresh_snapshot())
                await asyncio.wait_for(baseline_seen.wait(), 1)
                fail_response.set()
                older_result, newer_result = await asyncio.wait_for(
                    asyncio.gather(first, newer, return_exceptions=True), 2
                )
                assert isinstance(older_result, SnapshotStoreUnavailable)
                assert isinstance(newer_result, TransactionSnapshot)
                assert newer_result.metrics.fetching_duration_ms == 2
                assert fetches == 2
                assert await coordinator.read_revision() != first_revision
                assert await store.get_snapshot() == newer_result
            finally:
                fail_response.set()
                await worker.aclose()
                await asyncio.gather(first, return_exceptions=True)

    asyncio.run(check())
