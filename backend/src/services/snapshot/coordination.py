"""Bounded refresh leases and owner-checked publication on a single Redis server."""

import asyncio
import logging
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from redis.exceptions import RedisError

from services.snapshot.models import TransactionSnapshot
from services.snapshot.redis_store import RedisSnapshotStore, SnapshotStoreUnavailable
from services.snapshot.serialization import (
    InvalidSnapshot,
    deserialize_snapshot,
    serialize_snapshot,
)
from services.snapshot.store import snapshot_is_stale

logger = logging.getLogger(__name__)
REDIS_OPERATION_TIMEOUT_SECONDS = 2

# MSET publishes the pair in one command. The subsequent expiry/delete commands
# have validated integer arguments and cannot encounter wrong-type errors.
PUBLISH_SCRIPT = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
local retention = tonumber(ARGV[3])
if not retention or retention < 1 or retention > 2147483647 or retention ~= math.floor(retention) then
    return redis.error_reply('Invalid snapshot retention')
end
redis.call('MSET', KEYS[1], ARGV[2], KEYS[3], ARGV[1])
redis.call('EXPIRE', KEYS[1], retention)
redis.call('EXPIRE', KEYS[3], retention)
redis.call('DEL', KEYS[2])
return 1
"""
RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True)
class RefreshState:
    snapshot: TransactionSnapshot | None
    revision: str | None


class SnapshotRefreshTimeout(SnapshotStoreUnavailable):
    """The caller's finite refresh budget expired."""


class SnapshotRefreshCoordinator(Protocol):
    wait_timeout_seconds: float

    async def read_state(self) -> RefreshState: ...

    async def refresh(
        self,
        fetch: Callable[[], Awaitable[TransactionSnapshot]],
        *,
        force_refresh: bool,
        baseline_revision: str | None,
        max_age_seconds: int,
    ) -> TransactionSnapshot: ...


class RedisRefreshCoordinator:
    def __init__(
        self,
        store: RedisSnapshotStore,
        *,
        lease_seconds: float = 210,
        wait_timeout_seconds: float = 220,
        poll_seconds: tuple[float, float] = (0.2, 0.4),
    ) -> None:
        if lease_seconds <= 0 or wait_timeout_seconds <= 0:
            raise ValueError("Refresh lease and timeout must be positive")
        if poll_seconds[0] <= 0 or poll_seconds[1] < poll_seconds[0]:
            raise ValueError("Invalid refresh polling interval")
        self.store = store
        self.redis = store.redis
        self.lock_key = f"{store.key}:refresh-lock"
        self.revision_key = f"{store.key}:revision"
        self.lease_ms = math.ceil(lease_seconds * 1000)
        self.wait_timeout_seconds = wait_timeout_seconds
        self.poll_seconds = poll_seconds

    @staticmethod
    def _revision(value: bytes | str | None) -> str | None:
        try:
            revision = value.decode("ascii") if isinstance(value, bytes) else value
        except UnicodeDecodeError:
            revision = None
        if (
            revision is not None
            and len(revision) == 32
            and all(c in "0123456789abcdef" for c in revision)
        ):
            return revision
        return None

    async def read_state(self) -> RefreshState:
        try:
            async with asyncio.timeout(REDIS_OPERATION_TIMEOUT_SECONDS):
                # Read both values atomically as bytes, including malformed UTF-8.
                payload, revision = await self.redis.execute_command(
                    "MGET", self.store.key, self.revision_key, NEVER_DECODE=True
                )
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None
        snapshot = None
        if payload is not None:
            try:
                snapshot = deserialize_snapshot(payload)
            except InvalidSnapshot:
                logger.warning("Ignoring invalid transaction snapshot cache payload")
        return RefreshState(snapshot, self._revision(revision))

    async def read_revision(self) -> str | None:
        try:
            async with asyncio.timeout(REDIS_OPERATION_TIMEOUT_SECONDS):
                value = await self.redis.execute_command(
                    "GET", self.revision_key, NEVER_DECODE=True
                )
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None
        return self._revision(value)

    async def acquire(self, token: str) -> bool:
        try:
            async with asyncio.timeout(REDIS_OPERATION_TIMEOUT_SECONDS):
                return bool(
                    await self.redis.set(
                        self.lock_key, token, nx=True, px=self.lease_ms
                    )
                )
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None

    async def publish(self, token: str, snapshot: TransactionSnapshot) -> bool:
        payload = serialize_snapshot(snapshot)
        try:
            async with asyncio.timeout(REDIS_OPERATION_TIMEOUT_SECONDS):
                return bool(
                    await self.redis.eval(
                        PUBLISH_SCRIPT,
                        3,
                        self.store.key,
                        self.lock_key,
                        self.revision_key,
                        token,
                        payload,
                        self.store.retention_seconds,
                    )
                )
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None

    async def release(self, token: str) -> None:
        try:
            async with asyncio.timeout(REDIS_OPERATION_TIMEOUT_SECONDS):
                await self.redis.eval(RELEASE_SCRIPT, 1, self.lock_key, token)
        except (RedisError, OSError):
            raise SnapshotStoreUnavailable(
                "Transaction snapshot cache unavailable"
            ) from None

    @staticmethod
    def _usable(
        state: RefreshState,
        *,
        force_refresh: bool,
        baseline_revision: str | None,
        max_age_seconds: int,
    ) -> bool:
        if state.snapshot is None or snapshot_is_stale(state.snapshot, max_age_seconds):
            return False
        return not force_refresh or (
            state.revision is not None and state.revision != baseline_revision
        )

    async def refresh(
        self,
        fetch: Callable[[], Awaitable[TransactionSnapshot]],
        *,
        force_refresh: bool,
        baseline_revision: str | None,
        max_age_seconds: int,
    ) -> TransactionSnapshot:
        try:
            async with asyncio.timeout(self.wait_timeout_seconds):
                while True:
                    token = uuid4().hex
                    if await self.acquire(token):
                        published = False
                        try:
                            # Another owner could have published just before our SET NX.
                            state = await self.read_state()
                            if self._usable(
                                state,
                                force_refresh=force_refresh,
                                baseline_revision=baseline_revision,
                                max_age_seconds=max_age_seconds,
                            ):
                                assert state.snapshot is not None
                                return state.snapshot
                            candidate = await fetch()
                            published = await self.publish(token, candidate)
                            if published:
                                return candidate
                            # Lease expired. Never return this worker's rejected candidate.
                        finally:
                            if not published:
                                try:
                                    await self.release(token)
                                except SnapshotStoreUnavailable:
                                    # Preserve a fetch/cancellation error; the finite lease
                                    # will recover ownership even if cleanup cannot reach Redis.
                                    logger.warning(
                                        "Snapshot refresh lease cleanup failed; waiting for expiry"
                                    )
                    else:
                        revision = await self.read_revision()
                        if revision is not None and revision != baseline_revision:
                            state = await self.read_state()
                            if self._usable(
                                state,
                                force_refresh=force_refresh,
                                baseline_revision=baseline_revision,
                                max_age_seconds=max_age_seconds,
                            ):
                                assert state.snapshot is not None
                                return state.snapshot
                    await asyncio.sleep(random.uniform(*self.poll_seconds))
        except TimeoutError:
            raise SnapshotRefreshTimeout(
                "Transaction snapshot refresh timed out"
            ) from None
