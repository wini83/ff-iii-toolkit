"""Shared job records. Execution remains local; expired leases never resume work."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from time import time
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

RETENTION_SECONDS = 24 * 3600
LEASE_SECONDS = 120
MAX_RECORD_BYTES = 2 * 1024 * 1024
INTERRUPTED = (
    "Execution interrupted; verify results and run a new preview before retrying"
)


class JobStoreUnavailable(RuntimeError):
    pass


class JobLeaseLost(RuntimeError):
    pass


class JobBusy(RuntimeError):
    pass


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    id: str
    token: str = Field(default_factory=lambda: uuid4().hex)
    owner: str
    kind: str
    scope: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    data: dict
    deadline: float = 0
    finished: float | None = None
    error: str | None = None
    in_flight: int | None = None


def encoded(record: Record) -> str:
    envelope = record.model_dump(exclude={"data"})
    # Keep nested JSON opaque to Lua: cjson otherwise turns empty arrays into objects.
    try:
        envelope["data"] = json.dumps(record.data, allow_nan=False)
        raw = json.dumps(envelope, allow_nan=False)
    except (ValueError, TypeError):
        raise JobStoreUnavailable("Invalid job state") from None
    if len(raw.encode()) > MAX_RECORD_BYTES:
        raise JobStoreUnavailable("Job state too large")
    return raw


class JobStore(Protocol):
    lease_seconds: float

    async def claim(self, record: Record) -> tuple[Record, bool]: ...
    async def get(self, job_id: str) -> Record | None: ...
    async def current(self, scope: str) -> Record | None: ...
    async def save(self, record: Record) -> None: ...
    async def heartbeat(self, job_id: str, token: str) -> None: ...


@dataclass
class _Entry:
    record: Record
    expires: float


class MemoryJobStore:
    def __init__(
        self, *, lease_seconds=LEASE_SECONDS, retention=RETENTION_SECONDS, clock=time
    ):
        self.lease_seconds = lease_seconds
        self.retention = retention
        self.clock = clock
        self.entries: dict[str, _Entry] = {}
        self.scopes: dict[str, str] = {}
        self.lock = asyncio.Lock()

    def _get(self, job_id: str) -> Record | None:
        entry = self.entries.get(job_id)
        now = self.clock()
        if entry is None:
            return None
        if entry.expires <= now:
            self.entries.pop(job_id, None)
            return None
        record = entry.record
        if record.status in {"pending", "running"} and record.deadline <= now:
            record.status, record.error, record.finished = "failed", INTERRUPTED, now
            entry.expires = now + self.retention
        return record.model_copy(deep=True)

    async def claim(self, record: Record) -> tuple[Record, bool]:
        encoded(record)
        async with self.lock:
            # Remove expired data and pointers even when clients never poll them.
            for job_id in list(self.entries):
                self._get(job_id)
            self.scopes = {s: j for s, j in self.scopes.items() if j in self.entries}
            old = self._get(self.scopes.get(record.scope, ""))
            if old and old.status in {"pending", "running"}:
                return old, False
            if record.id in self.entries:
                raise JobStoreUnavailable("Job already exists")
            record = record.model_copy(deep=True)
            record.deadline = self.clock() + self.lease_seconds
            self.entries[record.id] = _Entry(record, self.clock() + self.retention)
            self.scopes[record.scope] = record.id
            return record.model_copy(deep=True), True

    async def get(self, job_id: str) -> Record | None:
        async with self.lock:
            return self._get(job_id)

    async def current(self, scope: str) -> Record | None:
        async with self.lock:
            return self._get(self.scopes.get(scope, ""))

    async def save(self, record: Record) -> None:
        encoded(record)
        async with self.lock:
            old = self._get(record.id)
            if (
                not old
                or old.token != record.token
                or old.status not in {"pending", "running"}
            ):
                raise JobLeaseLost("Job execution no longer active")
            saved = record.model_copy(deep=True)
            saved.deadline = self.clock() + self.lease_seconds
            if saved.status in {"done", "failed"}:
                saved.finished = self.clock()
            self.entries[record.id] = _Entry(saved, self.clock() + self.retention)

    async def heartbeat(self, job_id: str, token: str) -> None:
        async with self.lock:
            record = self._get(job_id)
            if (
                not record
                or record.token != token
                or record.status not in {"pending", "running"}
            ):
                raise JobLeaseLost("Job execution no longer active")
            self.entries[job_id].record.deadline = self.clock() + self.lease_seconds
            self.entries[job_id].expires = self.clock() + self.retention


# All keys use the same hash tag. Redis time avoids worker clock skew. Every
# mutation checks the current execution token and lease before accepting state.
COMMON = """
local now = tonumber(redis.call('TIME')[1])
local function load(key)
    local raw = redis.call('GET', key)
    if not raw then return nil end
    local r = cjson.decode(raw)
    if (r.status == 'pending' or r.status == 'running') and r.deadline <= now then
        r.status = 'failed'; r.error = ARGV[1]; r.finished = now
        redis.call('SET', key, cjson.encode(r), 'EX', ARGV[2])
    end
    return r
end
"""
GET = (
    "#!lua\n"
    + COMMON
    + """
local r = load(KEYS[1])
return r and cjson.encode(r) or false
"""
)
CURRENT = (
    "#!lua\n"
    + COMMON
    + """
local id = redis.call('GET', KEYS[1])
if not id then return false end
local r = load(ARGV[3] .. id)
return r and cjson.encode(r) or false
"""
)
CLAIM = (
    "#!lua\n"
    + COMMON
    + """
local id = redis.call('GET', KEYS[1])
if id then
    local old = load(ARGV[3] .. id)
    if old and (old.status == 'pending' or old.status == 'running') then
        return {cjson.encode(old), 0}
    end
end
if redis.call('EXISTS', KEYS[2]) == 1 then return redis.error_reply('duplicate job') end
local r = cjson.decode(ARGV[4]); r.deadline = now + tonumber(ARGV[5])
local raw = cjson.encode(r)
redis.call('SET', KEYS[2], raw, 'EX', ARGV[2])
redis.call('SET', KEYS[1], r.id, 'EX', ARGV[2])
return {raw, 1}
"""
)
SAVE = (
    "#!lua\n"
    + COMMON
    + """
local old = load(KEYS[1])
if not old or old.token ~= ARGV[3] or (old.status ~= 'pending' and old.status ~= 'running') then return 0 end
local r
if ARGV[4] == '' then r = old else r = cjson.decode(ARGV[4]) end
r.deadline = now + tonumber(ARGV[5])
if r.status == 'done' or r.status == 'failed' then r.finished = now end
redis.call('SET', KEYS[1], cjson.encode(r), 'EX', ARGV[2])
-- Keep the current pointer alive during long computations without changing it.
if redis.call('GET', KEYS[2]) == r.id then redis.call('EXPIRE', KEYS[2], ARGV[2]) end
return 1
"""
)


class RedisJobStore:
    def __init__(
        self,
        redis: Redis,
        *,
        namespace="ff-iii-toolkit:{jobs-v1}",
        lease_seconds=LEASE_SECONDS,
        retention=RETENTION_SECONDS,
    ):
        self.redis, self.namespace = redis, namespace
        self.lease_seconds, self.retention = lease_seconds, retention

    @property
    def prefix(self):
        return self.namespace + ":record:"

    def scope_key(self, scope):
        return self.namespace + ":scope:" + hashlib.sha256(scope.encode()).hexdigest()

    async def _eval(self, script, keys, *args):
        try:
            async with asyncio.timeout(2):
                return await self.redis.eval(
                    script, len(keys), *keys, INTERRUPTED, self.retention, *args
                )
        except (RedisError, OSError, TimeoutError, UnicodeDecodeError):
            raise JobStoreUnavailable("Job storage unavailable") from None

    def _decode(self, raw):
        try:
            if not raw:
                return None
            envelope = json.loads(raw)
            envelope["data"] = json.loads(envelope["data"])
            return Record.model_validate(envelope)
        except (ValueError, TypeError, KeyError):
            raise JobStoreUnavailable("Invalid job state") from None

    async def claim(self, record: Record) -> tuple[Record, bool]:
        raw, claimed = await self._eval(
            CLAIM,
            [self.scope_key(record.scope), self.prefix + record.id],
            self.prefix,
            encoded(record),
            self.lease_seconds,
        )
        return self._decode(raw), bool(claimed)

    async def get(self, job_id: str) -> Record | None:
        return self._decode(await self._eval(GET, [self.prefix + job_id]))

    async def current(self, scope: str) -> Record | None:
        return self._decode(
            await self._eval(CURRENT, [self.scope_key(scope)], self.prefix)
        )

    async def save(self, record: Record) -> None:
        if not await self._eval(
            SAVE,
            [self.prefix + record.id, self.scope_key(record.scope)],
            record.token,
            encoded(record),
            self.lease_seconds,
        ):
            raise JobLeaseLost("Job execution no longer active")

    async def heartbeat(self, job_id: str, token: str) -> None:
        # The scope is checked from stored state, never supplied by a caller.
        record = await self.get(job_id)
        if not record:
            raise JobLeaseLost("Job execution no longer active")
        if not await self._eval(
            SAVE,
            [self.prefix + job_id, self.scope_key(record.scope)],
            token,
            "",
            self.lease_seconds,
        ):
            raise JobLeaseLost("Job execution no longer active")
