from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from ff_iii_luciferin.api import FireflyAPIError
from ff_iii_luciferin.domain.models import SystemInfo

from api.routers.system import get_transaction_snapshot_service
from main import get_version
from services.domain.metrics import FetchMetrics
from services.guards import require_active_user
from services.snapshot.models import TransactionSnapshot
from settings import settings


def test_system_ping(client):
    r = client.get("/api/system/health")
    assert r.status_code == 200


def test_system_version(client):
    r = client.get("/api/system/version")
    assert r.status_code == 200


def test_release_version_overrides_component_version(monkeypatch):
    monkeypatch.setenv("TOOLKIT_VERSION", "3.0.1")

    assert get_version() == "3.0.1"


def test_transaction_snapshot_status_without_snapshot(client):
    service = AsyncMock()
    service.max_age_seconds = 86400
    service.get_cached_snapshot = AsyncMock(return_value=None)

    client.app.dependency_overrides[get_transaction_snapshot_service] = lambda: service

    r = client.get("/api/system/transaction-snapshot")

    assert r.status_code == 200
    assert r.json()["ttl_seconds"] == 86400
    assert r.json()["has_snapshot"] is False
    assert r.json()["snapshot_fetched_at"] is None
    assert r.json()["expires_at"] is None
    assert r.json()["is_stale"] is True


def test_transaction_snapshot_status_with_snapshot(client):
    fetched_at = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
    snapshot = TransactionSnapshot(
        transactions=[],
        metrics=FetchMetrics(
            total_transactions=0,
            fetching_duration_ms=10,
            invalid=0,
            multipart=0,
        ),
        fetched_at=fetched_at,
    )
    store = AsyncMock()
    store.is_stale = AsyncMock(return_value=False)
    service = AsyncMock()
    service.max_age_seconds = 86400
    service.get_cached_snapshot = AsyncMock(return_value=snapshot)
    service.store = store

    client.app.dependency_overrides[get_transaction_snapshot_service] = lambda: service

    r = client.get("/api/system/transaction-snapshot")

    assert r.status_code == 200
    assert r.json()["ttl_seconds"] == 86400
    assert r.json()["has_snapshot"] is True
    assert r.json()["snapshot_fetched_at"] == "2026-03-26T10:00:00Z"
    assert r.json()["expires_at"] == "2026-03-27T10:00:00Z"
    assert r.json()["is_stale"] is False
    assert r.json()["transaction_count"] == 0
    assert r.json()["schema_version"] == 1


def test_bootstrap_happy_path(client):
    r = client.post(
        "/api/system/bootstrap",
        json={"username": "admin", "password": "secret"},
    )
    assert r.status_code == 201


def test_bootstrap_only_once(client):
    client.post(
        "/api/system/bootstrap",
        json={"username": "admin", "password": "secret"},
    )
    r = client.post(
        "/api/system/bootstrap",
        json={"username": "admin2", "password": "secret"},
    )
    assert r.status_code == 409


def test_transaction_snapshot_refresh_rejects_missing_internal_api_key(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "internal-secret")

    r = client.post("/api/system/transaction-snapshot/refresh")

    assert r.status_code == 401


def test_transaction_snapshot_refresh_rejects_invalid_internal_api_key(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "internal-secret")

    r = client.post(
        "/api/system/transaction-snapshot/refresh",
        headers={"X-Internal-Api-Key": "wrong-secret"},
    )

    assert r.status_code == 401


def test_transaction_snapshot_refresh_returns_snapshot_metadata(client, monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "internal-secret")
    fetched_at = datetime(2026, 3, 26, 10, 0, tzinfo=UTC)
    snapshot = TransactionSnapshot(
        transactions=[],
        metrics=FetchMetrics(
            total_transactions=0,
            fetching_duration_ms=10,
            invalid=0,
            multipart=0,
        ),
        fetched_at=fetched_at,
    )
    service = AsyncMock()
    service.max_age_seconds = 86400
    service.refresh_snapshot = AsyncMock(return_value=snapshot)

    client.app.dependency_overrides[get_transaction_snapshot_service] = lambda: service

    r = client.post(
        "/api/system/transaction-snapshot/refresh",
        headers={"X-Internal-Api-Key": "internal-secret"},
    )

    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "refreshed": True,
        "ttl_seconds": 86400,
        "snapshot_fetched_at": "2026-03-26T10:00:00Z",
        "expires_at": "2026-03-27T10:00:00Z",
        "transaction_count": 0,
        "schema_version": 1,
        "timestamp": r.json()["timestamp"],
    }
    service.refresh_snapshot.assert_awaited_once_with()


def test_firefly_status_requires_authentication(client):
    assert client.get("/api/system/firefly").status_code == 401


@pytest.mark.parametrize("missing", ["FIREFLY_URL", "FIREFLY_TOKEN"])
def test_firefly_status_not_configured(client, monkeypatch, missing):
    client.app.dependency_overrides[require_active_user] = lambda: "user"
    monkeypatch.setattr(settings, "FIREFLY_URL", "https://firefly.example")
    monkeypatch.setattr(settings, "FIREFLY_TOKEN", "test-token")
    monkeypatch.setattr(settings, missing, "")
    factory = AsyncMock()
    monkeypatch.setattr("api.routers.system.get_firefly_client", factory)

    response = client.get("/api/system/firefly")

    assert response.status_code == 200
    assert response.json()["status"] == "not_configured"
    factory.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_firefly_status_uses_luciferin_about(client, monkeypatch, failed):
    client.app.dependency_overrides[require_active_user] = lambda: "user"
    monkeypatch.setattr(settings, "FIREFLY_URL", "https://firefly.example")
    monkeypatch.setattr(settings, "FIREFLY_TOKEN", "test-token")
    firefly = AsyncMock()
    firefly.get_about.return_value = SystemInfo(
        version="6.7.4", api_version="6.7.4", php_version=None, os=None, driver=None
    )
    if failed:
        firefly.get_about.side_effect = FireflyAPIError(
            "secret error test-token", status_code=401
        )
    monkeypatch.setattr("api.routers.system.get_firefly_client", lambda: firefly)

    response = client.get("/api/system/firefly")

    assert response.status_code == 200
    assert response.json()["status"] == ("error" if failed else "ok")
    assert response.json()["version"] == (None if failed else "6.7.4")
    assert response.json()["api_version"] == (None if failed else "6.7.4")
    assert "test-token" not in response.text
    firefly.get_about.assert_awaited_once_with()
