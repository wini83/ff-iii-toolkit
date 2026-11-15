"""Optional Redis connection infrastructure; no application stores use it yet."""

from collections.abc import AsyncGenerator, Awaitable
from contextlib import asynccontextmanager
from typing import cast

from pydantic import SecretStr
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError


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
            # redis-py shares the PING annotation with its synchronous client.
            await cast(Awaitable[bool], client.ping())
        except (RedisError, OSError, ValueError):
            raise RuntimeError("Redis startup readiness check failed") from None
        yield client
    finally:
        if client is not None:
            await client.aclose()


async def redis_health(client: Redis | None) -> str:
    if client is None:
        return "not_configured"
    try:
        await cast(Awaitable[bool], client.ping())
    except (RedisError, OSError):
        return "error"
    return "ok"
