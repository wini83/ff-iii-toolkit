import asyncio
import json
import os
import sys
from uuid import uuid4

import pytest
from pydantic import SecretStr

from services.domain.metrics import FetchMetrics
from services.infrastructure.redis import redis_connection
from services.snapshot.coordination import RedisRefreshCoordinator
from services.snapshot.redis_store import RedisSnapshotStore
from services.snapshot.service import TransactionSnapshotService

API_SCRIPT = r"""
import asyncio, json, os, sys
from types import SimpleNamespace
from fastapi.testclient import TestClient
import api.deps_services as deps
from main import create_app
from services.domain.metrics import FetchMetrics
from services.snapshot.coordination import RedisRefreshCoordinator

key = os.environ["TRANSACTION_SNAPSHOT_REDIS_KEY"]
class ObservedCoordinator(RedisRefreshCoordinator):
    first_read = True
    async def read_state(self):
        state = await super().read_state()
        if self.first_read:
            self.first_read = False
            await self.redis.incr(key + ":ready")
        return state
deps.RedisRefreshCoordinator = ObservedCoordinator
async def fetch():
    redis = app.state.redis
    await redis.incr(key + ":fetch-count")
    async with asyncio.timeout(5):
        while await redis.get(key + ":ready") != "2":
            await asyncio.sleep(.005)
    return [], FetchMetrics(0, 1, 0, 0)
deps.get_firefly_base_service = lambda: SimpleNamespace(fetch_transactions_with_metrics=fetch)
app = create_app()
with TestClient(app) as client:
    if sys.argv[1] == "force":
        response = client.post("/api/system/transaction-snapshot/refresh", headers={"X-Internal-Api-Key": "test-key"})
        assert response.status_code == 200, response.text
        result = response.json()
    else:
        # Status initializes the real API dependency without triggering a fetch.
        response = client.get("/api/system/transaction-snapshot")
        assert response.status_code == 200, response.text
        value = client.portal.call(app.state.transaction_snapshot_service.get_snapshot)
        result = {"snapshot_fetched_at": value.fetched_at.isoformat(), "transaction_count": value.transaction_count}
print(json.dumps(result))
"""

CRASH_SCRIPT = r"""
import asyncio, os
from pydantic import SecretStr
from services.infrastructure.redis import redis_connection
from services.snapshot.redis_store import RedisSnapshotStore
from services.snapshot.coordination import RedisRefreshCoordinator
async def main():
    async with redis_connection(SecretStr(os.environ["TEST_REDIS_URL"])) as redis:
        coordinator = RedisRefreshCoordinator(RedisSnapshotStore(redis, key=os.environ["TRANSACTION_SNAPSHOT_REDIS_KEY"]), lease_seconds=1)
        assert await coordinator.acquire("dead-owner")
        print("locked", flush=True)
        await asyncio.Event().wait()
asyncio.run(main())
"""


@pytest.mark.parametrize("mode", ["get", "force"])
def test_two_api_processes_publish_one_shared_snapshot(mode):
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for multi-process Redis tests")
    key = f"toolkit:test:process-coord:{uuid4().hex}"
    env = os.environ | {
        "REDIS_URL": url,
        "SECRET_KEY": "test-secret",
        "INTERNAL_API_KEY": "test-key",
        "TRANSACTION_SNAPSHOT_REDIS_KEY": key,
        "TRANSACTION_SNAPSHOT_REFRESH_LEASE_SECONDS": "10",
        "TRANSACTION_SNAPSHOT_REFRESH_TIMEOUT_SECONDS": "10",
        "PYTHONPATH": "src",
    }

    async def check():
        async with redis_connection(SecretStr(url)) as redis:
            processes = []
            try:
                for _ in range(2):
                    processes.append(
                        await asyncio.create_subprocess_exec(
                            sys.executable,
                            "-c",
                            API_SCRIPT,
                            mode,
                            env=env,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                    )
                results = await asyncio.wait_for(
                    asyncio.gather(*[p.communicate() for p in processes]), 20
                )
                for process, (_out, err) in zip(processes, results, strict=True):
                    assert process.returncode == 0, err.decode()
                payloads = [json.loads(out) for out, _ in results]
                assert (
                    payloads[0]["snapshot_fetched_at"]
                    == payloads[1]["snapshot_fetched_at"]
                )
                assert await redis.get(key + ":fetch-count") == "1"
                assert await redis.get(key + ":refresh-lock") is None
                assert await redis.get(key + ":revision") is not None
            finally:
                for process in processes:
                    if process.returncode is None:
                        process.kill()
                        await process.wait()
                await redis.delete(
                    key,
                    key + ":revision",
                    key + ":refresh-lock",
                    key + ":ready",
                    key + ":fetch-count",
                )

    asyncio.run(check())


def test_killed_lock_owner_is_recovered_after_lease_expires():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for multi-process Redis tests")
    key = f"toolkit:test:crash-coord:{uuid4().hex}"
    env = os.environ | {"TRANSACTION_SNAPSHOT_REDIS_KEY": key, "PYTHONPATH": "src"}

    async def check():
        async with redis_connection(SecretStr(url)) as redis:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                CRASH_SCRIPT,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            store = RedisSnapshotStore(redis, key=key, retention_seconds=60)
            coordinator = RedisRefreshCoordinator(
                store,
                lease_seconds=1,
                wait_timeout_seconds=3,
                poll_seconds=(0.005, 0.01),
            )
            try:
                line = await asyncio.wait_for(process.stdout.readline(), 10)
                assert line == b"locked\n"
                process.kill()
                await process.wait()
                assert await redis.get(coordinator.lock_key) == "dead-owner"

                class Firefly:
                    calls = 0

                    async def fetch_transactions_with_metrics(self):
                        self.calls += 1
                        return [], FetchMetrics(0, 1, 0, 0)

                firefly = Firefly()
                worker = TransactionSnapshotService(
                    store, firefly, coordinator=coordinator
                )
                result = await asyncio.wait_for(worker.get_snapshot(), 5)
                assert firefly.calls == 1
                assert result == await store.get_snapshot()
                assert await redis.get(coordinator.lock_key) is None
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                await redis.delete(key, coordinator.revision_key, coordinator.lock_key)

    asyncio.run(check())
