import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError, OutOfMemoryError

from services.domain.metrics import FetchMetrics
from services.infrastructure.redis import redis_connection
from services.snapshot.models import TransactionSnapshot
from services.snapshot.redis_store import RedisSnapshotStore, SnapshotStoreUnavailable
from services.snapshot.serialization import serialize_snapshot
from services.snapshot.service import TransactionSnapshotService


def snapshot(*, stale=False):
    return TransactionSnapshot(
        transactions=[],
        metrics=FetchMetrics(0, 1, 0, 0),
        fetched_at=datetime.now(UTC) - timedelta(seconds=301 if stale else 0),
    )


@pytest.mark.parametrize(
    "payload",
    [
        "{broken",
        "null",
        "{}",
        '{"schema_version":999}',
        '{"schema_version":1,"fetched_at":"bad"}',
    ],
)
def test_corrupt_payload_is_a_cache_miss_without_deletion(payload, caplog):
    redis = AsyncMock()
    redis.get.return_value = payload
    store = RedisSnapshotStore(redis)
    assert asyncio.run(store.get_snapshot()) is None
    assert asyncio.run(store.is_stale(300))
    redis.delete.assert_not_awaited()
    assert payload not in caplog.text


def test_unknown_version_of_otherwise_valid_payload_is_not_used():
    redis = AsyncMock()
    data = json.loads(serialize_snapshot(snapshot()))
    data["schema_version"] = 2
    redis.get.return_value = json.dumps(data)
    assert asyncio.run(RedisSnapshotStore(redis).get_snapshot()) is None


@pytest.mark.parametrize("operation", ["get", "set", "invalidate", "stale"])
def test_connection_failures_are_explicit_and_sanitized(operation):
    redis = AsyncMock()
    error = ConnectionError("secret redis://user:password@host")
    redis.get.side_effect = error
    redis.set.side_effect = error
    redis.delete.side_effect = error
    store = RedisSnapshotStore(redis)

    async def check():
        with pytest.raises(SnapshotStoreUnavailable) as exc:
            if operation == "get":
                await store.get_snapshot()
            elif operation == "set":
                await store.set_snapshot(snapshot())
            elif operation == "stale":
                await store.is_stale(300)
            else:
                await store.invalidate()
        assert "password" not in str(exc.value)

    asyncio.run(check())


def test_oom_does_not_return_new_snapshot_as_success():
    redis = AsyncMock()
    redis.set.side_effect = OutOfMemoryError("memory limit")
    firefly = MagicMock()
    firefly.fetch_transactions_with_metrics = AsyncMock(
        return_value=([], FetchMetrics(0, 1, 0, 0))
    )
    service = TransactionSnapshotService(RedisSnapshotStore(redis), firefly)
    with pytest.raises(SnapshotStoreUnavailable):
        asyncio.run(service.refresh_snapshot())


def test_failed_refresh_never_returns_old_financial_data():
    redis = AsyncMock()
    redis.get.return_value = serialize_snapshot(snapshot(stale=True))
    firefly = MagicMock()
    firefly.fetch_transactions_with_metrics = AsyncMock(
        side_effect=RuntimeError("upstream failed")
    )
    service = TransactionSnapshotService(RedisSnapshotStore(redis), firefly)
    with pytest.raises(RuntimeError, match="upstream failed"):
        asyncio.run(service.get_snapshot())


def test_operation_timeout_does_not_fall_back_to_memory(monkeypatch):
    monkeypatch.setattr(
        "services.snapshot.redis_store.SNAPSHOT_REDIS_TIMEOUT_SECONDS", 0.01
    )

    async def stall(*args):
        await asyncio.Event().wait()

    redis = AsyncMock()
    redis.get.side_effect = stall
    with pytest.raises(SnapshotStoreUnavailable):
        asyncio.run(RedisSnapshotStore(redis).get_snapshot())


def test_real_redis_key_expiry_and_recovery_after_corruption():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for Redis integration tests")

    async def check():
        async with redis_connection(SecretStr(url)) as redis:
            key = f"toolkit:test:expiry:{uuid4().hex}"
            store = RedisSnapshotStore(redis, key=key, retention_seconds=60)
            try:
                await redis.set(key, "malformed", ex=60)
                assert await store.get_snapshot() is None
                valid = snapshot(stale=True)
                await store.set_snapshot(valid)
                assert 0 < await redis.ttl(key) <= 60
                assert await store.is_stale(300)
                assert await store.get_snapshot() == valid
                await redis.pexpire(key, 1)
                await asyncio.sleep(0.02)
                assert await store.get_snapshot() is None
                await store.set_snapshot(snapshot())
                assert not await store.is_stale(300)
            finally:
                await store.invalidate()

    asyncio.run(check())


PROCESS_SCRIPT = r"""
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
import api.deps_services as deps
from main import create_app
from services.domain.metrics import FetchMetrics
from services.domain.transaction import Transaction, TxType, Currency
from services.snapshot.redis_store import RedisSnapshotStore

mode = sys.argv[1]
tx = Transaction(date(2026, 11, 15), Decimal("12.340000001"), 42,
    TxType.WITHDRAWAL, "Process restart", {"tag"}, None, None,
    Currency("PLN", "zl", 2))
fetch = AsyncMock(return_value=([tx], FetchMetrics(1, 2, 0, 0)))
if mode == "read":
    fetch.side_effect = AssertionError("Must reuse Redis after API restart")
deps.get_firefly_base_service = lambda: SimpleNamespace(fetch_transactions_with_metrics=fetch)
app = create_app()
with TestClient(app) as client:
    assert isinstance(app.state.snapshot_store, RedisSnapshotStore)
    if mode == "write":
        response = client.post("/api/system/transaction-snapshot/refresh",
            headers={"X-Internal-Api-Key": "test-internal-key"})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
        fetched_at = response.json()["snapshot_fetched_at"]
    else:
        response = client.get("/api/system/transaction-snapshot")
        assert response.status_code == 200, response.text
        assert response.json()["transaction_count"] == 1
        assert not response.json()["is_stale"]
        result = client.portal.call(app.state.transaction_snapshot_service.get_snapshot)
        assert result.transactions == [tx]
        fetch.assert_not_awaited()
        fetched_at = response.json()["snapshot_fetched_at"]
print(fetched_at)
"""


def test_snapshot_survives_restart_of_api_process():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for API process restart test")
    key = f"toolkit:test:restart:{uuid4().hex}"
    env = os.environ | {
        "REDIS_URL": url,
        "SECRET_KEY": "test-process-key",
        "INTERNAL_API_KEY": "test-internal-key",
        "TRANSACTION_SNAPSHOT_REDIS_KEY": key,
        "TRANSACTION_SNAPSHOT_TTL_SECONDS": "300",
        "TRANSACTION_SNAPSHOT_RETENTION_SECONDS": "60",
        "PYTHONPATH": "src",
    }

    async def cleanup():
        async with redis_connection(SecretStr(url)) as redis:
            await redis.delete(key)

    try:
        timestamps = []
        for mode in ["write", "read"]:
            result = subprocess.run(
                [sys.executable, "-c", PROCESS_SCRIPT, mode],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            timestamps.append(result.stdout.strip())
        assert timestamps[0] == timestamps[1]
    finally:
        asyncio.run(cleanup())
