"""Temporary Redis previews with atomic, globally bounded admission/eviction."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

from services.velobank_import.parser import VeloBankStatement
from services.velobank_import.session import (
    MAX_PREVIEWS_GLOBAL,
    MAX_PREVIEWS_PER_USER,
    PREVIEW_TTL_SECONDS,
    ImportPreview,
    PreviewNotFound,
    PreviewStoreUnavailable,
    deserialize_preview,
    serialize_preview,
)

logger = logging.getLogger(__name__)

# One index of at most 100 owner:id members avoids persistent per-user indexes.
# Scores are deadlines, also giving oldest-first eviction for a fixed TTL.
# Every key shares a hash tag. This script requires no SCAN, KEYS or background job.
# Explicit script flags (no allow-oom) make Redis reject OOM before any writes.
# Legacy EVAL mode can bypass OOM checks after the initial index cleanup.
CREATE = """#!lua
local index, prefix = KEYS[1], ARGV[1]
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local ttl = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', index, '-inf', now)
local members = redis.call('ZRANGE', index, 0, -1)
local own = {}
for _, member in ipairs(members) do
    if redis.call('EXISTS', prefix .. member) == 0 then
        redis.call('ZREM', index, member)
    elseif string.sub(member, 1, string.len(ARGV[2])) == ARGV[2] then
        table.insert(own, member)
    end
end
local function discard(member)
    redis.call('DEL', prefix .. member)
    redis.call('ZREM', index, member)
end
while #own >= tonumber(ARGV[6]) do
    discard(table.remove(own, 1))
end
while redis.call('ZCARD', index) >= tonumber(ARGV[7]) do
    discard(redis.call('ZRANGE', index, 0, 0)[1])
end
redis.call('SET', KEYS[2], ARGV[4], 'EX', ttl)
redis.call('ZADD', index, now + ttl, ARGV[3])
redis.call('EXPIRE', index, ttl)
return 1
"""

# Keep deletion available during OOM: neither command allocates preview data.
DELETE = """
local removed = redis.call('DEL', KEYS[2])
redis.call('ZREM', KEYS[1], ARGV[1])
return removed
"""


class RedisVeloBankPreviewStore:
    def __init__(
        self,
        redis: Redis,
        *,
        namespace: str = "ff-iii-toolkit:{velobank-preview-v1}",
        ttl_seconds: int = PREVIEW_TTL_SECONDS,
    ):
        if not namespace or ttl_seconds <= 0:
            raise ValueError("Preview namespace and positive TTL required")
        self.redis = redis
        self.prefix = namespace + ":data:"
        self.index = namespace + ":index"
        self.ttl_seconds = ttl_seconds

    def _key(self, key: UUID, owner: UUID) -> str:
        return self.prefix + f"{owner}:{key}"

    async def _execute(self, operation, *args):
        try:
            async with asyncio.timeout(2):
                return await operation(*args)
        except (RedisError, OSError, TimeoutError):
            raise PreviewStoreUnavailable("Preview storage unavailable") from None

    async def create(self, owner: UUID, statement: VeloBankStatement) -> ImportPreview:
        entry = ImportPreview(
            uuid4(),
            owner,
            statement,
            datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
        )
        payload = serialize_preview(entry)
        # Enforce the same wire validation as reads before touching Redis.
        entry = deserialize_preview(payload)
        await self._execute(
            self.redis.eval,
            CREATE,
            2,
            self.index,
            self._key(entry.id, owner),
            self.prefix,
            f"{owner}:",
            f"{owner}:{entry.id}",
            payload,
            self.ttl_seconds,
            MAX_PREVIEWS_PER_USER,
            MAX_PREVIEWS_GLOBAL,
        )
        return entry

    async def get(self, key: UUID, owner: UUID) -> ImportPreview:
        try:
            payload = await self._execute(self.redis.get, self._key(key, owner))
            if payload is None:
                raise PreviewNotFound()
            entry = deserialize_preview(payload)
            if (
                entry.id != key
                or entry.owner != owner
                or entry.expires_at <= datetime.now(UTC)
            ):
                raise PreviewNotFound()
            return entry
        except (ValueError, UnicodeDecodeError):
            # Never log bank data or delete a possibly concurrently replaced key.
            logger.warning("Ignoring invalid VeloBank preview payload")
            raise PreviewNotFound() from None

    async def delete(self, key: UUID, owner: UUID) -> None:
        # Validate ownership and expiry before an atomic payload/index removal.
        await self.get(key, owner)
        removed = await self._execute(
            self.redis.eval,
            DELETE,
            2,
            self.index,
            self._key(key, owner),
            f"{owner}:{key}",
        )
        if not removed:
            raise PreviewNotFound()
