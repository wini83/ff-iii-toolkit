import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr

from services.domain.metrics import FetchMetrics
from services.domain.transaction import (
    AccountRef,
    AccountType,
    Category,
    Currency,
    FXContext,
    Transaction,
    TxType,
)
from services.infrastructure.redis import redis_connection
from services.snapshot.models import TransactionSnapshot
from services.snapshot.redis_store import RedisSnapshotStore
from services.snapshot.serialization import InvalidSnapshot
from services.snapshot.store import InMemorySnapshotStore

pytestmark = pytest.mark.parametrize("backend", ["memory", "redis"])


def sample_snapshot(fetched_at=None):
    return TransactionSnapshot(
        transactions=[
            Transaction(
                id=42,
                date=date(2026, 11, 15),
                amount=Decimal("123456789.123456789"),
                type=TxType.TRANSFER,
                description="Przelew — zażółć",
                tags={"custom", "blik_done"},
                notes="line 1\nline 2",
                category=Category(id=2, name="Dom"),
                currency=Currency(code="PLN", symbol="zł", decimals=2),
                fx=FXContext(
                    Currency(code="EUR", symbol="€", decimals=2), Decimal("12.3400")
                ),
                source_account=AccountRef(1, "Bank", AccountType.ASSET, "PL123"),
                destination_account=AccountRef(2, "Cash", AccountType.ASSET),
            )
        ],
        metrics=FetchMetrics(
            total_transactions=4, fetching_duration_ms=20, invalid=1, multipart=2
        ),
        fetched_at=fetched_at or datetime.now(UTC),
    )


@asynccontextmanager
async def store_case(backend):
    if backend == "memory":
        yield InMemorySnapshotStore()
        return
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for real Redis contract tests")
    async with redis_connection(SecretStr(url)) as redis:
        store = RedisSnapshotStore(
            redis, key=f"toolkit:test:snapshot:{uuid4().hex}", retention_seconds=60
        )
        try:
            yield store
        finally:
            await store.invalidate()


def test_set_get_replace_and_invalidate(backend):
    async def check():
        async with store_case(backend) as store:
            assert await store.get_snapshot() is None
            assert await store.is_stale(300)
            snapshot = sample_snapshot()
            await store.set_snapshot(snapshot)
            assert await store.get_snapshot() == snapshot
            assert not await store.is_stale(300)
            replacement = replace(snapshot, transactions=[])
            await store.set_snapshot(replacement)
            assert await store.get_snapshot() == replacement
            await store.invalidate()
            await store.invalidate()
            assert await store.get_snapshot() is None
            assert await store.is_stale(300)

    asyncio.run(check())


def test_freshness_uses_fetched_at_and_configurable_age(backend):
    async def check():
        async with store_case(backend) as store:
            snapshot = sample_snapshot(datetime.now(UTC) - timedelta(seconds=301))
            await store.set_snapshot(snapshot)
            assert await store.is_stale(300)
            assert not await store.is_stale(600)
            # A freshly written key must not make historical data fresh.
            assert await store.get_snapshot() == snapshot

    asyncio.run(check())


def test_timezone_offset_round_trip(backend):
    async def check():
        async with store_case(backend) as store:
            snapshot = sample_snapshot(datetime.now(timezone(timedelta(hours=2))))
            await store.set_snapshot(snapshot)
            result = await store.get_snapshot()
            assert result == snapshot
            assert result.fetched_at.utcoffset() == timedelta(hours=2)
            assert not await store.is_stale(300)

    asyncio.run(check())


@pytest.mark.parametrize("invalid", ["version", "timestamp", "transaction", "metrics"])
def test_invalid_snapshot_is_rejected_without_replacing_good_data(backend, invalid):
    async def check():
        async with store_case(backend) as store:
            valid = sample_snapshot()
            await store.set_snapshot(valid)
            if invalid == "version":
                bad = replace(valid, schema_version=999)
            elif invalid == "timestamp":
                bad = replace(valid, fetched_at=datetime.now())
            elif invalid == "transaction":
                bad = replace(
                    valid, transactions=[replace(valid.transactions[0], amount=1.25)]
                )
            else:
                bad = replace(valid, metrics=FetchMetrics("bad", 1, 0, 0))
            with pytest.raises(InvalidSnapshot):
                await store.set_snapshot(bad)
            assert await store.get_snapshot() == valid

    asyncio.run(check())
