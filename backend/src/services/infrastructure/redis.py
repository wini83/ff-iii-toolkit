"""Optional Redis connection infrastructure; no application stores use it yet."""

import asyncio
from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from typing import cast

from pydantic import SecretStr
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

REDIS_PROBE_TIMEOUT_SECONDS = 2


async def _ping(client: Redis) -> None:
    # Bound the entire probe, including DNS, handshake and URL-specified timeouts.
    async with asyncio.timeout(REDIS_PROBE_TIMEOUT_SECONDS):
        # redis-py shares the PING annotation with its synchronous client.
        await cast(Awaitable[bool], client.ping())


@asynccontextmanager
async def redis_connection(url: SecretStr | None) -> AsyncGenerator[Redis | None]:
    if url is None:
        yield None
        return

    # Keep readiness probes bounded, with no implicit retries or credential logging.
    client = None
    try:
        try:
            client = Redis.from_url(
                url.get_secret_value(),
                socket_connect_timeout=2,
                socket_timeout=2,
                retry=Retry(NoBackoff(), 0),
                max_connections=10,
                decode_responses=True,
            )
            await _ping(client)
        except (RedisError, OSError, ValueError, TimeoutError):
            raise RuntimeError("Redis startup readiness check failed") from None
        yield client
    finally:
        if client is not None:
            await client.aclose()


async def redis_health(client: Redis | None) -> str:
    if client is None:
        return "not_configured"
    try:
        await _ping(client)
    except (RedisError, OSError, TimeoutError):
        return "error"
    return "ok"
