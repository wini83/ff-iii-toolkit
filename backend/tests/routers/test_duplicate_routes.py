import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.deps_services import get_transaction_snapshot_service
from services.domain.metrics import FetchMetrics
from services.domain.transaction import TxType
from services.firefly_base_service import FireflyServiceError
from services.guards import require_active_user
from services.snapshot.coordination import SnapshotRefreshTimeout
from services.snapshot.models import TransactionSnapshot
from services.snapshot.service import TransactionSnapshotService
from services.snapshot.store import InMemorySnapshotStore
from tests.services.test_duplicate_detection import BANK, DAY, OTHER_BANK, tx

STAMP = datetime(2026, 11, 18, tzinfo=UTC)


@pytest.fixture
def duplicates_client(client):
    snapshot = TransactionSnapshot(
        [
            tx(1),
            tx(2, tags={"csv"}, day=2),
            tx(3, tags={"third"}, type=TxType.TRANSFER, destination_account=OTHER_BANK),
        ],
        FetchMetrics(6, 10, 0, 3),
        STAMP,
    )
    service = MagicMock()
    service.get_snapshot = AsyncMock(return_value=snapshot)
    service.get_cached_snapshot = AsyncMock(return_value=snapshot)
    service.refresh_snapshot = AsyncMock(return_value=snapshot)
    client.app.dependency_overrides[require_active_user] = lambda: "user"
    client.app.dependency_overrides[get_transaction_snapshot_service] = lambda: service
    yield client, service


def query(**changes):
    return {
        "account_id": 1,
        "date_from": DAY.isoformat(),
        "date_to": (DAY + timedelta(days=2)).isoformat(),
        "snapshot_timestamp": STAMP.isoformat(),
    } | changes


def test_options_and_stable_paged_results(duplicates_client):
    client, service = duplicates_client
    options = client.get("/api/tx/duplicates/options").json()
    assert [a["id"] for a in options["accounts"]] == [BANK.id, OTHER_BANK.id]
    assert options["date_from"] == DAY.isoformat()
    assert options["skipped_multipart"] == 3
    pages = [
        client.get("/api/tx/duplicates", params=query(limit=1, offset=i))
        for i in range(3)
    ]
    assert all(p.status_code == 200 for p in pages)
    assert [p.json()["items"][0]["id"] for p in pages] == ["1:2", "2:3", "1:3"]
    assert all(p.json()["total"] == 3 for p in pages)
    pair = pages[0].json()["items"][0]
    assert pair["amount"] == "12.34"
    assert pair["date_difference_days"] == 2
    assert pair["direction"] == "outgoing"
    assert pair["first"]["source_account"]["id"] == 1
    assert pair["second"]["tags"] == ["csv"]
    assert pages[0].json()["skipped_multipart"] == 3
    assert service.get_snapshot.await_count == 1


@pytest.mark.parametrize("missing", [False, True])
def test_changed_snapshot_requires_new_search_without_firefly_fetch(
    duplicates_client, missing
):
    client, service = duplicates_client
    if missing:
        service.get_cached_snapshot.return_value = None
    else:
        service.get_cached_snapshot.return_value.fetched_at += timedelta(seconds=1)
    response = client.get("/api/tx/duplicates", params=query())
    assert response.status_code == 409
    service.get_snapshot.assert_not_awaited()


@pytest.mark.parametrize(
    "changes",
    [
        {"account_id": 0},
        {"tolerance_days": -1},
        {"tolerance_days": 8},
        {"tolerance_days": 1.5},
        {"date_from": "2026-11-30"},
        {"date_to": "bad"},
        {"limit": 101},
        {"offset": -1},
        {"snapshot_timestamp": "bad"},
    ],
)
def test_invalid_queries(duplicates_client, changes):
    client, _ = duplicates_client
    assert client.get("/api/tx/duplicates", params=query(**changes)).status_code == 422


def test_unknown_account_has_no_candidates(duplicates_client):
    client, _ = duplicates_client
    response = client.get("/api/tx/duplicates", params=query(account_id=123))
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.parametrize(
    "error,status",
    [
        (FireflyServiceError("failed"), 502),
        (SnapshotRefreshTimeout("timeout"), 504),
        (TimeoutError("timeout"), 504),
    ],
)
@pytest.mark.parametrize("refresh", [False, True])
def test_options_fetch_failure(duplicates_client, error, status, refresh):
    client, service = duplicates_client
    if refresh:
        service.refresh_snapshot.side_effect = error
        response = client.post("/api/tx/duplicates/options/refresh")
    else:
        service.get_snapshot.side_effect = error
        response = client.get("/api/tx/duplicates/options")
    assert response.status_code == status


@pytest.mark.parametrize("path", ["/api/tx/duplicates/options", "/api/tx/duplicates"])
def test_auth_required(client, path):
    assert client.get(path, params=query()).status_code == 401


def test_refresh_requires_authentication(client):
    assert client.post("/api/tx/duplicates/options/refresh").status_code == 401


def test_explicit_refresh_replaces_fresh_cached_candidates(duplicates_client):
    client, mock_service = duplicates_client
    cached = replace(
        mock_service.get_snapshot.return_value, fetched_at=datetime.now(UTC)
    )
    store = InMemorySnapshotStore()
    asyncio.run(store.set_snapshot(cached))
    firefly = MagicMock()
    # A user corrected the tags in Firefly: these pairs no longer qualify.
    firefly.fetch_transactions_with_metrics = AsyncMock(
        return_value=(
            [replace(tx, tags={"corrected"}) for tx in cached.transactions],
            cached.metrics,
        )
    )
    service = TransactionSnapshotService(store, firefly, max_age_seconds=300)
    client.app.dependency_overrides[get_transaction_snapshot_service] = lambda: service
    initial = client.get("/api/tx/duplicates/options")
    assert initial.status_code == 200
    old_stamp = initial.json()["snapshot_timestamp"]
    firefly.fetch_transactions_with_metrics.assert_not_awaited()
    assert (
        client.get(
            "/api/tx/duplicates", params=query(snapshot_timestamp=old_stamp)
        ).json()["total"]
        == 3
    )

    refreshed = client.post("/api/tx/duplicates/options/refresh")
    assert refreshed.status_code == 200
    new_stamp = refreshed.json()["snapshot_timestamp"]
    assert new_stamp != old_stamp
    firefly.fetch_transactions_with_metrics.assert_awaited_once()
    assert (
        client.get(
            "/api/tx/duplicates", params=query(snapshot_timestamp=old_stamp)
        ).status_code
        == 409
    )
    assert (
        client.get(
            "/api/tx/duplicates", params=query(snapshot_timestamp=new_stamp)
        ).json()["total"]
        == 0
    )


@pytest.mark.parametrize("refresh", [False, True])
def test_in_memory_fetch_timeout_returns_504(client, monkeypatch, refresh):
    monkeypatch.setattr(
        "services.snapshot.service.SNAPSHOT_FETCH_TIMEOUT_SECONDS", 0.001
    )

    async def never_completes():
        await asyncio.Event().wait()

    firefly = MagicMock()
    firefly.fetch_transactions_with_metrics = never_completes
    service = TransactionSnapshotService(InMemorySnapshotStore(), firefly)
    client.app.dependency_overrides[require_active_user] = lambda: "user"
    client.app.dependency_overrides[get_transaction_snapshot_service] = lambda: service
    response = (
        client.post("/api/tx/duplicates/options/refresh")
        if refresh
        else client.get("/api/tx/duplicates/options")
    )
    assert response.status_code == 504
    assert response.json()["detail"] == "Transaction snapshot refresh timed out"
