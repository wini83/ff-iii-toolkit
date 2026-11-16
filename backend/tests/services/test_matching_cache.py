"""Memory/Redis contracts using synthetic financial data, never real cookies."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError, OutOfMemoryError

from services.domain.allegro import AllegroPageMatchCacheEntry, AllegroPageRequest
from services.domain.bank_record import BankRecord
from services.domain.match_result import MatchResult
from services.infrastructure.redis import redis_connection
from services.matching_cache.backend import (
    MAX_FIELDS,
    MAX_PAYLOAD_BYTES,
    METADATA_TTL,
    MatchCacheUnavailable,
    MemoryMatchBackend,
    RedisMatchBackend,
)
from services.matching_cache.stores import AllegroMatchStore, BlikMatchStore
from tests.services.test_allegro_application_service import _payment, _tx


@asynccontextmanager
async def backend(kind, *, env="TEST_REDIS_URL"):
    if kind == "memory":
        yield MemoryMatchBackend()
        return
    url = os.getenv(env)
    if not url:
        pytest.skip(f"Set {env} to run matching cache integration tests")
    async with redis_connection(SecretStr(url)) as redis:
        store = RedisMatchBackend(redis, namespace=f"test:{{matching-{uuid4().hex}}}")
        try:
            yield store
        finally:
            keys = [key async for key in redis.scan_iter(match=store.namespace + ":*")]
            if keys:
                await redis.delete(*keys)


def blik_matches():
    tx = _tx(42)
    tx.amount = Decimal("-12.340000001")
    tx.notes = "Synthetic note"
    bank = BankRecord(
        date=date(2026, 1, 2),
        amount=tx.amount,
        details="Synthetic details",
        recipient="Synthetic shop",
        operation_amount=tx.amount,
    )
    return [MatchResult(tx, [bank])]


def page_entry(offset=0):
    return AllegroPageMatchCacheEntry(
        page=AllegroPageRequest(25, offset),
        login="synthetic-login",
        payments=[_payment("payment-1", "full-1")],
        matches=[MatchResult(_tx(offset + 1), [_payment("payment-1", "full-1")])],
        fetched_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_feature_roundtrips_and_cross_user_isolation(kind):
    async def check():
        async with backend(kind) as b:
            owner, outsider, secret = uuid4(), uuid4(), uuid4()
            blik, allegro = BlikMatchStore(b), AllegroMatchStore(b)
            assert (
                await blik.get(owner=owner, file_id="file", revision="digest") is None
            )
            assert (
                await allegro.get(
                    owner=owner,
                    secret_id=secret,
                    revision="r",
                    page=AllegroPageRequest(),
                )
                is None
            )
            original = blik_matches()
            assert await blik.put(
                owner=owner, file_id="file", revision="digest", matches=original
            )
            restored = await blik.get(owner=owner, file_id="file", revision="digest")
            assert restored == original
            assert restored[0].tx.amount == Decimal("-12.340000001")
            restored[0].tx.notes = "mutated caller copy"
            assert (
                await blik.get(owner=owner, file_id="file", revision="digest")
                == original
            )
            assert (
                await blik.get(owner=outsider, file_id="file", revision="digest")
                is None
            )
            assert (
                await blik.get(owner=owner, file_id="file", revision="changed-file")
                is None
            )
            entry = page_entry()
            assert await allegro.put(
                owner=owner, secret_id=secret, revision="r", entry=entry
            )
            assert (
                await allegro.get(
                    owner=owner, secret_id=secret, revision="r", page=entry.page
                )
                == entry.matches
            )
            assert (
                await allegro.get_all(owner=outsider, secret_id=secret, revision="r")
                == []
            )
            assert (
                await allegro.get_all(
                    owner=owner, secret_id=secret, revision="rotated-secret"
                )
                == []
            )
            assert not await allegro.invalidate(
                owner=outsider, secret_id=secret, revision="r"
            )
            assert (
                await allegro.get_all(owner=owner, secret_id=secret, revision="r")
                == entry.matches
            )

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_page_secret_and_global_invalidation_and_late_writer_fencing(kind):
    async def check():
        async with backend(kind) as b:
            scope, other = "allegro:owner:secret:r", "allegro:owner:other:r"
            ticket = await b.begin(scope)
            assert await b.put(scope, "page-a", ticket, "a")
            assert await b.put(scope, "page-b", ticket, "b")
            other_ticket = await b.begin(other)
            assert await b.put(other, "page-a", other_ticket, "other")
            assert await b.invalidate(scope, "page-a")
            assert await b.read(scope, "page-a") is None
            assert await b.read(scope, "page-b") == "b"
            assert not await b.put(scope, "page-a", ticket, "late stale result")
            new = await b.begin(scope)
            assert await b.put(scope, "page-a", new, "new")
            assert await b.invalidate(scope)
            assert await b.values(scope) == []
            await b.begin(scope)
            assert not await b.put(scope, "page-a", new, "late after recreate")
            assert await b.values(other) == ["other"]
            await b.invalidate_all()
            assert await b.values(other) == []
            assert not await b.put(
                other, "page-a", other_ticket, "late after global clear"
            )

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_apply_consumes_atomically_and_invalidates_other_feature(kind):
    async def check():
        async with backend(kind) as b:
            blik = await b.begin("blik")
            allegro = await b.begin("allegro")
            await b.put("blik", "matches", blik, "blik result")
            await b.put("allegro", "page:25:0", allegro, "allegro result")
            results = await asyncio.gather(*[b.take("allegro") for _ in range(8)])
            assert sum(bool(result) for result in results) == 1
            assert next(result for result in results if result) == ["allegro result"]
            assert await b.read("blik", "matches") is None
            assert not await b.put("blik", "matches", blik, "late result")
            fresh = await b.begin("blik")
            assert await b.put("blik", "matches", fresh, "recomputed")

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_fixed_page_ttl_does_not_slide_when_another_page_is_written(kind):
    async def check():
        async with backend(kind) as b:
            ticket = await b.begin("scope")
            await b.put("scope", "a", ticket, "a", ttl=1)
            await b.put("scope", "b", ticket, "b", ttl=3)
            await asyncio.sleep(1.1)
            assert await b.read("scope", "a") is None
            assert await b.read("scope", "b") == "b"
            assert await b.values("scope") == ["b"]
            if kind == "redis":
                assert 0 < await b.redis.ttl(b.key("scope")) <= METADATA_TTL

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_bounded_payload_and_page_count(kind):
    async def check():
        async with backend(kind) as b:
            ticket = await b.begin("scope")
            with pytest.raises(MatchCacheUnavailable):
                await b.put("scope", "large", ticket, "x" * (MAX_PAYLOAD_BYTES + 1))
            for i in range(MAX_FIELDS):
                assert await b.put("scope", str(i), ticket, "small")
            assert not await b.put("scope", "too-many", ticket, "small")
            assert len(await b.values("scope")) == MAX_FIELDS
            assert await b.put("scope", "0", ticket, "replacement")

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_allegro_page_matching_semantics_and_invalidation(kind):
    async def check():
        async with backend(kind) as b:
            owner, secret, other = uuid4(), uuid4(), uuid4()
            store = AllegroMatchStore(b)
            first, second = page_entry(0), page_entry(25)
            await store.put(owner=owner, secret_id=secret, revision="r", entry=first)
            await store.put(owner=owner, secret_id=secret, revision="r", entry=second)
            await store.put(owner=owner, secret_id=other, revision="r", entry=first)
            assert (
                await store.get_all(owner=owner, secret_id=secret, revision="r")
                == first.matches + second.matches
            )
            assert await store.invalidate(
                owner=owner, secret_id=secret, revision="r", page=first.page
            )
            assert (
                await store.get_all(owner=owner, secret_id=secret, revision="r")
                == second.matches
            )
            assert await store.invalidate(owner=owner, secret_id=secret, revision="r")
            assert (
                await store.get_all(owner=owner, secret_id=secret, revision="r") == []
            )
            assert (
                await store.get_all(owner=owner, secret_id=other, revision="r")
                == first.matches
            )

    asyncio.run(check())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(owner=str(uuid4())),
        lambda d: d.update(file_id="other"),
        lambda d: d.update(revision="other"),
        lambda d: d.update(schema_version=99),
        lambda d: d.pop("schema_version"),
        lambda d: d.update(matches=[{"tx": {}, "matches": [], "status": "new"}]),
    ],
)
def test_invalid_payload_is_a_miss_without_financial_data_logging(mutate, caplog):
    async def check():
        b = MemoryMatchBackend()
        owner = uuid4()
        store = BlikMatchStore(b)
        await store.put(owner=owner, file_id="f", revision="r", matches=blik_matches())
        scope = store.scope(owner, "f", "r")
        data = json.loads(await b.read(scope, "matches"))
        mutate(data)
        await b.put(scope, "matches", await b.begin(scope), json.dumps(data))
        assert await store.get(owner=owner, file_id="f", revision="r") is None
        assert "Synthetic" not in caplog.text

    asyncio.run(check())


def test_unknown_or_corrupt_allegro_page_fails_closed():
    async def check():
        b = MemoryMatchBackend()
        owner, secret = uuid4(), uuid4()
        store = AllegroMatchStore(b)
        await store.put(owner=owner, secret_id=secret, revision="r", entry=page_entry())
        scope = store.scope(owner, secret, "r")
        await b.put(scope, "invalid", await b.begin(scope), "not-json")
        assert await store.get_all(owner=owner, secret_id=secret, revision="r") == []

    asyncio.run(check())


@pytest.mark.parametrize(
    "action", ["begin", "read", "values", "take", "invalidate", "invalidate_all", "put"]
)
def test_redis_failure_never_silently_falls_back_or_logs_credentials(action):
    redis = AsyncMock()
    redis.eval.side_effect = ConnectionError("redis://secret:password@host")
    redis.set.side_effect = ConnectionError("redis://secret:password@host")
    b = RedisMatchBackend(redis)

    async def check():
        with pytest.raises(MatchCacheUnavailable) as exc:
            if action == "invalidate_all":
                await b.invalidate_all()
            elif action == "read":
                await b.read("scope", "slot")
            elif action == "put":
                from services.matching_cache.backend import Ticket

                await b.put("scope", "slot", Ticket("e", "r"), "payload")
            else:
                await getattr(b, action)("scope")
        assert "password" not in str(exc.value)

    asyncio.run(check())


def test_real_redis_independent_clients_and_concurrent_stale_puts():
    async def check():
        async with (
            backend("redis") as b,
            redis_connection(SecretStr(os.environ["TEST_REDIS_URL"])) as client,
        ):
            second = RedisMatchBackend(client, b.namespace)
            ticket = await b.begin("scope")
            assert await second.put("scope", "a", ticket, "first")
            assert await b.read("scope", "a") == "first"
            await second.invalidate("scope", "a")
            accepted = await asyncio.gather(
                *[b.put("scope", str(i), ticket, "stale") for i in range(8)]
            )
            assert accepted == [False] * 8
            assert await second.values("scope") == []
            current = await second.begin("scope")
            assert await b.put("scope", "a", current, "fresh")
            assert await second.read("scope", "a") == "fresh"

    asyncio.run(check())


def test_real_redis_memory_limit_blocks_mutations_without_losing_cached_data():
    async def check():
        async with backend("redis", env="TEST_REDIS_OOM_URL") as b:
            ticket = await b.begin("scope")
            await b.put("scope", "a", ticket, "cached")
            previous = await b.redis.hgetall(b.key("scope"))
            config = (await b.redis.config_get("maxmemory"))["maxmemory"]
            policy = (await b.redis.config_get("maxmemory-policy"))["maxmemory-policy"]
            try:
                await b.redis.config_set("maxmemory-policy", "noeviction")
                await b.redis.config_set("maxmemory", 1)
                with pytest.raises(OutOfMemoryError):
                    await b.redis.set(b.namespace + ":probe", "must fail")
                for mutate in [
                    b.begin("scope"),
                    b.put("scope", "a", ticket, "new"),
                    b.take("scope"),
                ]:
                    with pytest.raises(MatchCacheUnavailable):
                        await mutate
                assert await b.read("scope", "a") == "cached"
                assert await b.redis.hgetall(b.key("scope")) == previous
                # Cleanup and bounded generation markers must remain usable
                # during OOM after an already-completed financial mutation.
                assert await b.invalidate("scope", "a")
                assert await b.read("scope", "a") is None
                await b.invalidate_all()
                with pytest.raises(MatchCacheUnavailable):
                    await b.put("scope", "a", ticket, "stale")
            finally:
                await b.redis.config_set("maxmemory", config)
                await b.redis.config_set("maxmemory-policy", policy)

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_allegro_invalidate_all_preserves_blik_and_fences_only_allegro(kind):
    async def check():
        async with backend(kind) as b:
            owner, secret = uuid4(), uuid4()
            allegro, blik = AllegroMatchStore(b), BlikMatchStore(b)
            ticket = await allegro.begin(owner=owner, secret_id=secret, revision="r")
            await allegro.put(
                owner=owner,
                secret_id=secret,
                revision="r",
                entry=page_entry(),
                ticket=ticket,
            )
            await blik.put(
                owner=owner, file_id="f", revision="r", matches=blik_matches()
            )
            await allegro.invalidate_all()
            assert (
                await allegro.get_all(owner=owner, secret_id=secret, revision="r") == []
            )
            assert not await allegro.put(
                owner=owner,
                secret_id=secret,
                revision="r",
                entry=page_entry(),
                ticket=ticket,
            )
            assert (
                await blik.get(owner=owner, file_id="f", revision="r") == blik_matches()
            )

    asyncio.run(check())


def test_invalid_write_error_is_sanitized_before_reaching_storage():
    async def check():
        b = MemoryMatchBackend()
        store = BlikMatchStore(b)
        owner = uuid4()
        with pytest.raises(MatchCacheUnavailable) as exc:
            await store.put(
                owner=owner,
                file_id="f",
                revision="r",
                matches=[MatchResult(tx=object(), matches=[])],
            )
        assert str(exc.value) == "Invalid matching cache payload"
        assert await b.values(store.scope(owner, "f", "r")) == []

    asyncio.run(check())
