"""Synthetic bank data only; real Redis exercises Lua and worker concurrency."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError, OutOfMemoryError

from services.infrastructure.redis import redis_connection
from services.velobank_import.parser import VeloBankStatement, VeloBankTransaction
from services.velobank_import.redis_store import RedisVeloBankPreviewStore
from services.velobank_import.session import (
    MAX_PREVIEW_BYTES,
    ImportPreview,
    PreviewNotFound,
    PreviewStoreUnavailable,
    PreviewTooLarge,
    VeloBankPreviewStore,
    deserialize_preview,
    serialize_preview,
)


def statement(description="Synthetic merchant"):
    return VeloBankStatement(
        "0" * 26,
        [
            VeloBankTransaction(
                date(2026, 1, 2),
                date(2026, 1, 3),
                description,
                Decimal("-12.340000001"),
                "PLN",
            )
        ],
    )


def entry():
    return ImportPreview(
        uuid4(), uuid4(), statement(), datetime.now(UTC) + timedelta(minutes=30)
    )


def test_wire_roundtrip_preserves_decimals_dates_ids_and_timezone():
    original = entry()
    assert deserialize_preview(serialize_preview(original)) == original
    assert '"-12.340000001"' in serialize_preview(original)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(schema_version=2),
        lambda d: d.update(owner="bad-uuid"),
        lambda d: d["statement"].update(transactions=[]),
        lambda d: d["statement"].update(account="invalid"),
        lambda d: d["statement"]["transactions"][0].update(currency="oops"),
        lambda d: d["statement"]["transactions"][0].update(description=""),
        lambda d: d.update(expires_at="2026-01-01T12:00:00"),
        lambda d: d.update(statement={"account": "", "transactions": [{}]}),
        lambda d: d["statement"]["transactions"][0].update(amount="NaN"),
        lambda d: d["statement"]["transactions"][0].update(date="invalid"),
    ],
)
def test_invalid_payloads_are_rejected(mutate):
    data = json.loads(serialize_preview(entry()))
    mutate(data)
    with pytest.raises(ValueError):
        deserialize_preview(json.dumps(data))


@pytest.mark.parametrize(
    "payload", ["{broken", "null", "{}", b"\xff", '"' + "x" * MAX_PREVIEW_BYTES + '"']
)
def test_corrupt_payload_is_a_miss_without_logging_bank_data(payload, caplog):
    redis = AsyncMock()
    redis.get.return_value = payload
    with pytest.raises(PreviewNotFound):
        asyncio.run(RedisVeloBankPreviewStore(redis).get(uuid4(), uuid4()))
    redis.delete.assert_not_awaited()
    assert "Synthetic merchant" not in caplog.text


@pytest.mark.parametrize("mismatch", ["id", "owner", "expired"])
def test_valid_but_inaccessible_payload_is_not_returned(mismatch):
    original = entry()
    data = json.loads(serialize_preview(original))
    if mismatch == "expired":
        data["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    else:
        data[mismatch] = str(uuid4())
    redis = AsyncMock()
    redis.get.return_value = json.dumps(data)
    with pytest.raises(PreviewNotFound):
        asyncio.run(RedisVeloBankPreviewStore(redis).get(original.id, original.owner))


@pytest.mark.parametrize("operation", ["create", "get", "delete"])
@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("redis://private:password@host"),
        OutOfMemoryError("private"),
        TimeoutError("private"),
    ],
)
def test_redis_errors_are_explicit_and_sanitized(operation, error):
    original = entry()
    redis = AsyncMock()
    redis.get.return_value = serialize_preview(original)
    if operation == "get":
        redis.get.side_effect = error
    else:
        redis.eval.side_effect = error
    store = RedisVeloBankPreviewStore(redis)

    async def check():
        with pytest.raises(PreviewStoreUnavailable) as exc:
            if operation == "create":
                await store.create(original.owner, original.statement)
            else:
                await getattr(store, operation)(original.id, original.owner)
        assert "private" not in str(exc.value)

    asyncio.run(check())


def test_deadline_bounds_the_entire_redis_operation(monkeypatch):
    redis = AsyncMock()

    async def slow(*args):
        await asyncio.sleep(10)

    redis.get.side_effect = slow
    original_timeout = asyncio.timeout
    monkeypatch.setattr(
        "services.velobank_import.redis_store.asyncio.timeout",
        lambda _: original_timeout(0.01),
    )
    with pytest.raises(PreviewStoreUnavailable):
        asyncio.run(RedisVeloBankPreviewStore(redis).get(uuid4(), uuid4()))


def test_oversized_write_does_not_touch_redis_or_evict_memory_previews():
    redis = AsyncMock()

    async def check():
        owner = uuid4()
        memory = VeloBankPreviewStore()
        first = await memory.create(owner, statement())
        for store in [memory, RedisVeloBankPreviewStore(redis)]:
            with pytest.raises(PreviewTooLarge):
                await store.create(owner, statement("ą" * MAX_PREVIEW_BYTES))
        assert await memory.get(first.id, owner) == first

    asyncio.run(check())
    redis.eval.assert_not_awaited()


@asynccontextmanager
async def real_store(*, ttl=1800, url_env="TEST_REDIS_URL"):
    url = os.getenv(url_env)
    if not url:
        pytest.skip(f"Set {url_env} to exercise Redis Lua and TTL")
    async with redis_connection(SecretStr(url)) as redis:
        store = RedisVeloBankPreviewStore(
            redis, namespace=f"toolkit:{{test-preview-{uuid4().hex}}}", ttl_seconds=ttl
        )
        try:
            yield redis, store
        finally:
            keys = [key async for key in redis.scan_iter(match=store.prefix + "*")]
            await redis.delete(store.index, *keys)


def test_real_redis_oom_rejects_create_without_evicting_existing_previews():
    async def check():
        # This test changes server-wide memory settings, so it must use a
        # dedicated disposable Redis instance, never the normal test server.
        async with real_store(url_env="TEST_REDIS_OOM_URL") as (redis, store):
            owner = uuid4()
            previews = [await store.create(owner, statement()) for _ in range(5)]
            members = await redis.zrange(store.index, 0, -1, withscores=True)
            keys = [store._key(preview.id, owner) for preview in previews]
            payloads = await redis.mget(keys)
            memory = (await redis.config_get("maxmemory"))["maxmemory"]
            policy = (await redis.config_get("maxmemory-policy"))["maxmemory-policy"]
            probe_key = store.prefix + "oom-probe"
            try:
                await redis.config_set("maxmemory-policy", "noeviction")
                await redis.config_set("maxmemory", 1)
                # Confirm this is a real OOM condition, rather than a mocked
                # error. The legacy Lua script would still accept the create.
                with pytest.raises(OutOfMemoryError):
                    await redis.set(probe_key, "must not be stored")
                with pytest.raises(PreviewStoreUnavailable):
                    await store.create(owner, statement("x" * 1_000_000))
                assert (
                    await redis.zrange(store.index, 0, -1, withscores=True) == members
                )
                assert await redis.mget(keys) == payloads
                assert set(await redis.zrange(store.index, 0, -1)) == {
                    f"{owner}:{preview.id}" for preview in previews
                }
                assert (
                    len(
                        [key async for key in redis.scan_iter(match=store.prefix + "*")]
                    )
                    == 5
                )
                # Deleting a preview must remain possible during OOM.
                await store.delete(previews[0].id, owner)
                assert not await redis.exists(keys[0])
            finally:
                await redis.config_set("maxmemory", memory)
                await redis.config_set("maxmemory-policy", policy)
                await redis.delete(probe_key)
            recovered = await store.create(owner, statement())
            assert await store.get(recovered.id, owner) == recovered

    asyncio.run(check())


def test_real_redis_shares_payloads_enforces_owner_and_deletes_indexes():
    async def check():
        async with real_store() as (redis, store):
            other = RedisVeloBankPreviewStore(
                redis, namespace=store.index.removesuffix(":index")
            )
            owner = uuid4()
            preview = await store.create(owner, statement())
            assert await other.get(preview.id, owner) == preview
            ttl = await redis.pttl(store._key(preview.id, owner))
            for action in (other.get, other.delete):
                with pytest.raises(PreviewNotFound):
                    await action(preview.id, uuid4())
            assert await other.get(preview.id, owner) == preview
            assert 0 < await redis.pttl(store._key(preview.id, owner)) <= ttl
            await other.delete(preview.id, owner)
            assert await redis.zcard(store.index) == 0
            assert not await redis.exists(store._key(preview.id, owner))
            with pytest.raises(PreviewNotFound):
                await store.get(preview.id, owner)

    asyncio.run(check())


def test_real_redis_concurrent_instances_cannot_exceed_user_or_global_limits():
    async def check():
        async with (
            real_store() as (redis, store),
            redis_connection(SecretStr(os.environ["TEST_REDIS_URL"])) as second_redis,
        ):
            other = RedisVeloBankPreviewStore(
                second_redis, namespace=store.index.removesuffix(":index")
            )
            owner = uuid4()
            first = await store.create(owner, statement())
            # Exercise simultaneous requests within the production pool budget;
            # exhaustion of that separate budget is an explicit 503.
            slots = asyncio.Semaphore(8)

            async def create_concurrently(store, owner):
                async with slots:
                    return await store.create(owner, statement())

            results = await asyncio.gather(
                *[
                    create_concurrently(store if i % 2 else other, owner)
                    for i in range(25)
                ]
            )
            assert await redis.zcard(store.index) == 5
            assert (
                len([key async for key in redis.scan_iter(match=store.prefix + "*")])
                == 5
            )
            with pytest.raises(PreviewNotFound):
                await store.get(first.id, owner)
            results += await asyncio.gather(
                *[
                    create_concurrently(store if i % 2 else other, uuid4())
                    for i in range(120)
                ]
            )
            assert await redis.zcard(store.index) == 100
            assert (
                len([key async for key in redis.scan_iter(match=store.prefix + "*")])
                == 100
            )
            for member in await redis.zrange(store.index, 0, -1):
                assert await redis.exists(store.prefix + member)
            assert not await redis.exists(store._key(results[0].id, owner))

    asyncio.run(check())


def test_real_redis_ttl_expires_data_and_index_and_prunes_stale_members():
    async def check():
        async with real_store(ttl=1) as (redis, store):
            owner = uuid4()
            preview = await store.create(owner, statement())
            await redis.zadd(store.index, {"stale-owner:stale-id": 0})
            await store.create(owner, statement())
            assert await redis.zcard(store.index) == 2
            await asyncio.sleep(1.1)
            with pytest.raises(PreviewNotFound):
                await store.get(preview.id, owner)
            assert not await redis.exists(store.index)
            assert not [key async for key in redis.scan_iter(match=store.prefix + "*")]

    asyncio.run(check())


def test_real_redis_corruption_and_missing_data_do_not_leak_capacity():
    async def check():
        async with real_store() as (redis, store):
            owner = uuid4()
            preview = await store.create(owner, statement())
            await redis.set(store._key(preview.id, owner), "{bad-json", ex=1800)
            with pytest.raises(PreviewNotFound):
                await store.get(preview.id, owner)
            await redis.delete(store._key(preview.id, owner))
            await store.create(owner, statement())
            assert await redis.zcard(store.index) == 1

    asyncio.run(check())


PROCESS_SCRIPT = r"""
import sys
from functools import partial
from unittest.mock import MagicMock
from uuid import UUID
from fastapi.testclient import TestClient
import main
from api.deps_db import get_db
from services.guards import require_active_user
from services.velobank_import.redis_store import RedisVeloBankPreviewStore
from tests.services.velobank_import.test_preview_storage import statement

mode, namespace, owner_text = sys.argv[1:4]
owner = UUID(owner_text)
main.RedisVeloBankPreviewStore = partial(RedisVeloBankPreviewStore, namespace=namespace)
app = main.create_app()
app.dependency_overrides[require_active_user] = lambda: owner
db = MagicMock()
db.get.return_value = None
app.dependency_overrides[get_db] = lambda: db
with TestClient(app) as client:
    store = app.state.velobank_preview_store
    assert isinstance(store, RedisVeloBankPreviewStore)
    assert store.redis is app.state.redis
    if mode == "write":
        preview = client.portal.call(store.create, owner, statement())
        print(preview.id)
    else:
        file_id = sys.argv[4]
        url = "/api/tools/velobank/files/" + file_id
        response = client.get(url)
        assert response.status_code == 200, response.text
        assert response.json()["preview"][0]["amount"] == "-12.34"
        cached = client.portal.call(store.get, UUID(file_id), owner)
        assert cached.statement == statement()
        mapping = {"accounts": {"PL" + "0" * 26: "Synthetic account"}}
        first = client.post(url + "/export-csv", json=mapping)
        second = client.post(url + "/export-csv", json=mapping)
        assert first.status_code == second.status_code == 200, first.text
        assert first.content == second.content
        assert client.delete(url).status_code == 204
        assert client.get(url).status_code == 404
"""


def test_preview_survives_api_process_restart_and_exports_repeatably():
    import subprocess
    import sys

    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for API process restart test")
    namespace = f"toolkit:{{test-preview-restart-{uuid4().hex}}}"
    owner = str(uuid4())
    env = os.environ | {
        "REDIS_URL": url,
        "SECRET_KEY": "test-process-key",
        "PYTHONPATH": "src:.",
    }
    file_id = None
    try:
        for mode in ["write", "read"]:
            args = [sys.executable, "-c", PROCESS_SCRIPT, mode, namespace, owner]
            if file_id:
                args.append(file_id)
            result = subprocess.run(
                args, env=env, capture_output=True, text=True, timeout=30
            )
            assert result.returncode == 0, result.stderr + result.stdout
            if mode == "write":
                file_id = str(UUID(result.stdout.strip()))
    finally:

        async def cleanup():
            async with redis_connection(SecretStr(url)) as redis:
                keys = [key async for key in redis.scan_iter(match=namespace + ":*")]
                if keys:
                    await redis.delete(*keys)

        asyncio.run(cleanup())
