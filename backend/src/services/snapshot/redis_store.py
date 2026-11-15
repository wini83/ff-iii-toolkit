"""Redis snapshot cache: atomic JSON writes and explicit availability failures."""

import asyncio
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from services.snapshot.models import TransactionSnapshot
from services.snapshot.serialization import (
    InvalidSnapshot,
    deserialize_snapshot,
    serialize_snapshot,
)
from services.snapshot.store import SnapshotStore, snapshot_is_stale

logger = logging.getLogger(__name__)
SNAPSHOT_REDIS_TIMEOUT_SECONDS = 2


class SnapshotStoreUnavailable(RuntimeError):
    """Redis snapshot operations failed; callers must not fall back to stale data."""


class RedisSnapshotStore(SnapshotStore):
    def __init__(
        self,
        redis: Redis,
        *,
        key: str = "ff-iii-toolkit:snapshot:v1:transactions",
        retention_seconds: int = 86400,
    ) -> None:
        if retention_seconds <= 0:
            raise ValueError("Snapshot retention must be positive")
        if not key:
            raise ValueError("Snapshot Redis key must not be empty")
        self.redis = redis
        self.key = key
        self.retention_seconds = retention_seconds

    async def get_snapshot(self) -> TransactionSnapshot | None:
        try:
            async with asyncio.timeout(SNAPSHOT_REDIS_TIMEOUT_SECONDS):
                payload = await self.redis.get(self.key)
            if payload is None:
                return None
            return deserialize_snapshot(payload)
        except (InvalidSnapshot, UnicodeDecodeError):
            # Redis may decode UTF-8 before the JSON validator sees the value.
            # Do not delete here: another process may already have replaced the
            # bad value. A successful refresh atomically overwrites it instead.
            logger.warning("Ignoring invalid transaction snapshot cache payload")
            return None
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None

    async def set_snapshot(self, snapshot: TransactionSnapshot) -> None:
        payload = serialize_snapshot(snapshot)
        try:
            async with asyncio.timeout(SNAPSHOT_REDIS_TIMEOUT_SECONDS):
                await self.redis.set(self.key, payload, ex=self.retention_seconds)
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None

    async def invalidate(self) -> None:
        try:
            async with asyncio.timeout(SNAPSHOT_REDIS_TIMEOUT_SECONDS):
                await self.redis.delete(self.key)
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None

    async def is_stale(self, max_age_seconds: int) -> bool:
        return snapshot_is_stale(await self.get_snapshot(), max_age_seconds)
