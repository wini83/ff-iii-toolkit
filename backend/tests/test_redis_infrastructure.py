import asyncio
import os
import traceback
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis.exceptions import ConnectionError

from main import create_app
from services.infrastructure.redis import redis_connection, redis_health
from settings import settings


def test_disabled_redis_does_not_connect(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Redis should not be created without REDIS_URL")

    monkeypatch.setattr("services.infrastructure.redis.Redis.from_url", unexpected)

    async def check():
        async with redis_connection(None) as client:
            assert client is None
            assert await redis_health(client) == "not_configured"

    asyncio.run(check())


def test_lifespan_connects_and_closes_redis(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(settings, "REDIS_URL", SecretStr("redis://cache:6379/0"))
    monkeypatch.setattr(
        "services.infrastructure.redis.Redis.from_url", lambda *a, **kw: client
    )
    app = create_app()
    with TestClient(app):
        assert app.state.redis is client
        client.ping.assert_awaited_once()
    client.aclose.assert_awaited_once()
    assert app.state.redis is None


def test_startup_failure_is_sanitized_and_closes_client(monkeypatch):
    client = AsyncMock()
    client.ping.side_effect = ConnectionError("redis://user:private-password@host")
    monkeypatch.setattr(settings, "REDIS_URL", SecretStr("redis://cache:6379/0"))
    monkeypatch.setattr(
        "services.infrastructure.redis.Redis.from_url", lambda *a, **kw: client
    )
    with (
        pytest.raises(
            RuntimeError, match="Redis startup readiness check failed"
        ) as exc,
        TestClient(create_app()),
    ):
        pytest.fail("Startup must fail")
    assert "private-password" not in "".join(traceback.format_exception(exc.value))
    client.aclose.assert_awaited_once()


def test_health_reports_redis_failure_and_recovery(client):
    redis = AsyncMock()
    client.app.state.redis = redis
    redis.ping.side_effect = ConnectionError("private-password")
    response = client.get("/api/system/health")
    assert response.status_code == 503
    assert response.json()["external_services"]["redis"] == "error"
    assert "private-password" not in response.text
    redis.ping.side_effect = None
    response = client.get("/api/system/health")
    assert response.status_code == 200
    assert response.json()["external_services"]["redis"] == "ok"


def test_real_redis_connection_round_trip():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL to run Redis integration tests")

    async def check():
        async with redis_connection(SecretStr(url)) as client:
            assert client is not None
            assert await redis_health(client) == "ok"
            key = f"toolkit:test:{uuid4().hex}"
            try:
                await client.set(key, "connected", ex=30)
                assert await client.get(key) == "connected"
            finally:
                await client.delete(key)

    asyncio.run(check())
