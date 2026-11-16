"""Short-lived matching data with generation fencing against late writers."""

import asyncio
import hashlib
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic
from typing import Protocol
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

MATCH_TTL = 600
METADATA_TTL = 1800
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
MAX_FIELDS = 100


class MatchCacheUnavailable(RuntimeError):
    """Matching storage failed; never silently use process-local results."""


@dataclass(frozen=True)
class Ticket:
    epoch: str
    revision: str


class MatchBackend(Protocol):
    async def begin(self, scope: str) -> Ticket: ...
    async def put(
        self, scope: str, slot: str, ticket: Ticket, payload: str, ttl: int = MATCH_TTL
    ) -> bool: ...
    async def read(self, scope: str, slot: str) -> str | None: ...
    async def values(self, scope: str) -> list[str]: ...
    async def invalidate(self, scope: str, slot: str | None = None) -> bool: ...
    async def invalidate_all(self) -> None: ...
    async def invalidate_feature(self, feature: str) -> None: ...
    async def take(self, scope: str) -> list[str]: ...


@dataclass
class _Bucket:
    ticket: Ticket
    expires: float
    values: dict[str, tuple[str, float]] = field(default_factory=dict)


class MemoryMatchBackend:
    def __init__(self):
        self.epoch = uuid4().hex
        self.feature_epochs: dict[str, str] = {}
        self.buckets: dict[str, _Bucket] = {}
        self.lock = RLock()

    def _epoch(self, scope: str) -> str:
        return self.epoch + ":" + self.feature_epochs.get(feature_of(scope), "0")

    def _bucket(self, scope: str) -> _Bucket | None:
        bucket = self.buckets.get(scope)
        if bucket and (
            bucket.ticket.epoch != self._epoch(scope) or bucket.expires <= monotonic()
        ):
            self.buckets.pop(scope, None)
            bucket = None
        if bucket:
            bucket.values = {
                k: v for k, v in bucket.values.items() if v[1] > monotonic()
            }
        return bucket

    async def begin(self, scope: str) -> Ticket:
        with self.lock:
            # Bound abandoned scopes as well as individual cached results.
            for key in list(self.buckets):
                self._bucket(key)
            bucket = self._bucket(scope)
            if bucket is None:
                bucket = _Bucket(
                    Ticket(self._epoch(scope), uuid4().hex), monotonic() + METADATA_TTL
                )
                self.buckets[scope] = bucket
            bucket.expires = monotonic() + METADATA_TTL
            return bucket.ticket

    async def put(
        self, scope: str, slot: str, ticket: Ticket, payload: str, ttl: int = MATCH_TTL
    ) -> bool:
        validate_write(payload, ttl)
        with self.lock:
            bucket = self._bucket(scope)
            if bucket is None or bucket.ticket != ticket:
                return False
            if slot not in bucket.values and len(bucket.values) >= MAX_FIELDS:
                return False
            bucket.values[slot] = (payload, monotonic() + ttl)
            bucket.expires = monotonic() + METADATA_TTL
            return True

    async def read(self, scope: str, slot: str) -> str | None:
        with self.lock:
            bucket = self._bucket(scope)
            return bucket.values[slot][0] if bucket and slot in bucket.values else None

    async def values(self, scope: str) -> list[str]:
        with self.lock:
            bucket = self._bucket(scope)
            return [value[0] for value in bucket.values.values()] if bucket else []

    async def invalidate(self, scope: str, slot: str | None = None) -> bool:
        with self.lock:
            bucket = self._bucket(scope)
            if bucket is None:
                return False
            found = bool(bucket.values) if slot is None else slot in bucket.values
            if slot is None:
                self.buckets.pop(scope, None)
            else:
                bucket.ticket = Ticket(self._epoch(scope), uuid4().hex)
                bucket.values.pop(slot, None)
            return found

    async def take(self, scope: str) -> list[str]:
        with self.lock:
            bucket = self._bucket(scope)
            values = [v[0] for v in bucket.values.values()] if bucket else []
            if values:
                self.epoch = uuid4().hex
                self.buckets.clear()
            return values

    async def invalidate_feature(self, feature: str) -> None:
        if feature not in {"allegro", "blik"}:
            raise ValueError("Unknown matching feature")
        with self.lock:
            self.feature_epochs[feature] = uuid4().hex
            for scope in list(self.buckets):
                self._bucket(scope)

    async def invalidate_all(self) -> None:
        with self.lock:
            self.epoch = uuid4().hex
            self.buckets.clear()


def feature_of(scope: str) -> str:
    feature = scope.split(":", 1)[0]
    return feature if feature in {"blik", "allegro"} else "generic"


def validate_write(payload: str, ttl: int) -> None:
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise MatchCacheUnavailable("Matching cache payload too large")
    if not 0 < ttl <= METADATA_TTL:
        raise ValueError("Invalid matching cache TTL")


# Redis 8.2 supports per-hash-field TTL. Every financial payload has its own
# fixed deadline; updating one page never renews another page's lifetime.
# Explicit Lua headers enforce OOM checks BEFORE any mutation (#110 review).
BEGIN = """#!lua
local epoch = (redis.call('GET', KEYS[1]) or '0') .. ':' .. (redis.call('GET', KEYS[3]) or '0')
if redis.call('HGET', KEYS[2], '_epoch') ~= epoch
    or not redis.call('HGET', KEYS[2], '_rev') then
    redis.call('DEL', KEYS[2])
    redis.call('HSET', KEYS[2], '_epoch', epoch, '_rev', ARGV[1])
end
redis.call('EXPIRE', KEYS[2], ARGV[2])
return {epoch, redis.call('HGET', KEYS[2], '_rev')}
"""
PUT = """#!lua
if ((redis.call('GET', KEYS[1]) or '0') .. ':' .. (redis.call('GET', KEYS[3]) or '0')) ~= ARGV[1]
    or redis.call('HGET', KEYS[2], '_epoch') ~= ARGV[1]
    or redis.call('HGET', KEYS[2], '_rev') ~= ARGV[2] then return 0 end
if redis.call('HEXISTS', KEYS[2], ARGV[3]) == 0
    and redis.call('HLEN', KEYS[2]) >= tonumber(ARGV[7]) + 2 then return 0 end
redis.call('HSET', KEYS[2], ARGV[3], ARGV[4])
redis.call('HEXPIRE', KEYS[2], ARGV[5], 'FIELDS', 1, ARGV[3])
redis.call('EXPIRE', KEYS[2], ARGV[6])
return 1
"""
READ = """#!lua flags=no-writes
if redis.call('HGET', KEYS[2], '_epoch') ~= ((redis.call('GET', KEYS[1]) or '0') .. ':' .. (redis.call('GET', KEYS[3]) or '0')) then return false end
return redis.call('HGET', KEYS[2], ARGV[1])
"""
VALUES = """#!lua flags=no-writes
if redis.call('HGET', KEYS[2], '_epoch') ~= ((redis.call('GET', KEYS[1]) or '0') .. ':' .. (redis.call('GET', KEYS[3]) or '0')) then return {} end
local result = {}
local fields = redis.call('HGETALL', KEYS[2])
for i=1,#fields,2 do
    if string.sub(fields[i], 1, 2) == 'd:' then table.insert(result, fields[i+1]) end
end
return result
"""
# Invalidation may run during OOM: it deletes data and only replaces one
# fixed-size revision token. It can never allocate a financial payload.
INVALIDATE = """#!lua flags=allow-oom
if redis.call('HGET', KEYS[2], '_epoch') ~= ((redis.call('GET', KEYS[1]) or '0') .. ':' .. (redis.call('GET', KEYS[3]) or '0')) then return 0 end
if ARGV[1] == '' then
    local found = redis.call('HLEN', KEYS[2]) > 2
    redis.call('DEL', KEYS[2])
    return found and 1 or 0
end
local found = redis.call('HDEL', KEYS[2], ARGV[1])
redis.call('HSET', KEYS[2], '_rev', ARGV[2])
return found
"""


class RedisMatchBackend:
    def __init__(self, redis: Redis, namespace: str = "ff-iii-toolkit:{matching-v1}"):
        self.redis = redis
        self.namespace = namespace
        # A single persistent random epoch fences all old buckets. Payloads and
        # bucket revisions expire; this constant-size key deliberately does not.
        self.epoch_key = namespace + ":epoch"

    def key(self, scope: str) -> str:
        return self.namespace + ":scope:" + hashlib.sha256(scope.encode()).hexdigest()

    async def _run(self, operation, *args):
        try:
            async with asyncio.timeout(2):
                return await operation(*args)
        except (RedisError, OSError, TimeoutError, UnicodeDecodeError):
            raise MatchCacheUnavailable("Matching cache unavailable") from None

    async def _eval(self, script: str, scope: str, *args):
        return await self._run(
            self.redis.eval,
            script,
            3,
            self.epoch_key,
            self.key(scope),
            self.epoch_key + ":" + feature_of(scope),
            *args,
        )

    async def begin(self, scope: str) -> Ticket:
        epoch, revision = await self._eval(BEGIN, scope, uuid4().hex, METADATA_TTL)
        return Ticket(epoch, revision)

    async def put(
        self, scope: str, slot: str, ticket: Ticket, payload: str, ttl: int = MATCH_TTL
    ) -> bool:
        validate_write(payload, ttl)
        return bool(
            await self._eval(
                PUT,
                scope,
                ticket.epoch,
                ticket.revision,
                "d:" + slot,
                payload,
                ttl,
                METADATA_TTL,
                MAX_FIELDS,
            )
        )

    async def read(self, scope: str, slot: str) -> str | None:
        return await self._eval(READ, scope, "d:" + slot)

    async def values(self, scope: str) -> list[str]:
        return await self._eval(VALUES, scope)

    async def invalidate(self, scope: str, slot: str | None = None) -> bool:
        return bool(
            await self._eval(
                INVALIDATE, scope, "d:" + slot if slot is not None else "", uuid4().hex
            )
        )

    async def take(self, scope: str) -> list[str]:
        return await self._eval(TAKE, scope, uuid4().hex)

    async def invalidate_feature(self, feature: str) -> None:
        if feature not in {"allegro", "blik"}:
            raise ValueError("Unknown matching feature")
        await self._run(
            self.redis.eval, EPOCH, 1, self.epoch_key + ":" + feature, uuid4().hex
        )

    async def invalidate_all(self) -> None:
        await self._run(self.redis.eval, EPOCH, 1, self.epoch_key, uuid4().hex)


# Read the selected result set and invalidate all financial matching results in
# the same operation before scheduling an apply. No stale read/apply window.
TAKE = """#!lua
if redis.call('HGET', KEYS[2], '_epoch') ~= ((redis.call('GET', KEYS[1]) or '0') .. ':' .. (redis.call('GET', KEYS[3]) or '0')) then return {} end
local result = {}
local fields = redis.call('HGETALL', KEYS[2])
for i=1,#fields,2 do
    if string.sub(fields[i], 1, 2) == 'd:' then table.insert(result, fields[i+1]) end
end
if #result > 0 then redis.call('SET', KEYS[1], ARGV[1]) end
return result
"""


# A fixed-size, constant-count generation marker must remain writable during
# OOM so a completed financial update can fence pending matching computations.
# Unlike PUT/BEGIN/TAKE, this operation never stores financial data or admits jobs.
EPOCH = """#!lua flags=allow-oom
redis.call('SET', KEYS[1], ARGV[1])
return 1
"""
