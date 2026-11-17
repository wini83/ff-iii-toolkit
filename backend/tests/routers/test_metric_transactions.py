import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.deps_services import get_transaction_snapshot_service
from services.domain.metrics import FetchMetrics
from services.domain.transaction import (
    AccountRef,
    AccountType,
    Category,
    Currency,
    Transaction,
    TxType,
)
from services.guards import require_active_user
from services.snapshot.metrics import (
    SnapshotAllegroMetricsService,
    SnapshotBlikMetricsService,
)
from services.snapshot.models import TransactionSnapshot
from settings import settings

STAMP = datetime(2026, 11, 1, tzinfo=UTC)


def tx(id, description, *, tags=None, categorized=False, month=1):
    return Transaction(
        id=id,
        date=date(2026, month, 5),
        amount=Decimal(id),
        description=description,
        tags=tags or set(),
        notes=None,
        category=Category(1, "Food") if categorized else None,
        currency=Currency("PLN", "zł", 2),
        type=TxType.WITHDRAWAL,
        source_account=AccountRef(10, "Bank", AccountType.ASSET),
        destination_account=AccountRef(20, "Shop", AccountType.EXPENSE),
    )


@pytest.fixture
def metrics_client(client, monkeypatch):
    monkeypatch.setattr(settings, "BLIK_DESCRIPTION_FILTER", "blik")
    monkeypatch.setattr(settings, "FIREFLY_URL", "https://firefly.example")
    snapshot = TransactionSnapshot(
        [
            tx(1, "ALLEGRO shop"),
            tx(2, "allegro", categorized=True),
            tx(3, "allegro", tags={"allegro_done"}),
            tx(4, "other"),
            tx(5, "BLIK", tags={"other_tag"}),
            tx(6, "blik", month=2),
            tx(7, "blik", categorized=True),
            tx(8, "blik", tags={"blik_done"}),
            tx(9, "blik shop"),
        ],
        FetchMetrics(9, 10, 0, 0),
        STAMP,
    )
    service = MagicMock()
    service.get_cached_snapshot = AsyncMock(return_value=snapshot)
    service.get_snapshot = AsyncMock(return_value=snapshot)
    client.app.dependency_overrides[require_active_user] = lambda: "user"
    client.app.dependency_overrides[get_transaction_snapshot_service] = lambda: service
    yield client, service


@pytest.mark.parametrize("provider,expected", [("allegro", [2, 1]), ("blik", [6, 5])])
def test_details_agree_with_metrics_and_page_totals(metrics_client, provider, expected):
    client, service = metrics_client
    cls = (
        SnapshotAllegroMetricsService
        if provider == "allegro"
        else SnapshotBlikMetricsService
    )
    metrics = asyncio.run(
        cls(service, **{f"filter_desc_{provider}": provider}).fetch_metrics()
    )
    total = (
        metrics.not_processed_allegro_transactions
        if provider == "allegro"
        else metrics.not_processed_transactions
    )
    params = {"snapshot_timestamp": STAMP.isoformat(), "limit": 1}
    pages = [
        client.get(
            f"/api/{provider}/statistics/transactions", params={**params, "offset": n}
        )
        for n in range(2)
    ]
    assert all(r.status_code == 200 for r in pages)
    assert [r.json()["items"][0]["id"] for r in pages] == expected
    assert all(r.json()["total"] == total == 2 for r in pages)
    for month, count in metrics.not_processed_by_month.items():
        response = client.get(
            f"/api/{provider}/statistics/transactions",
            params={**params, "month": month},
        )
        assert response.json()["total"] == count
    item = pages[0].json()["items"][0]
    assert item["source_account"]["name"] == "Bank"
    assert item["destination_account"]["name"] == "Shop"
    assert item["currency_code"] == "PLN"
    assert pages[0].json()["firefly_url"] == "https://firefly.example"


@pytest.mark.parametrize("provider", ["allegro", "blik"])
@pytest.mark.parametrize("sort", ["date_asc", "amount_asc", "amount_desc"])
def test_sort_and_empty_selection(metrics_client, provider, sort):
    client, _ = metrics_client
    response = client.get(
        f"/api/{provider}/statistics/transactions",
        params={"snapshot_timestamp": STAMP.isoformat(), "sort": sort},
    )
    ids = [t["id"] for t in response.json()["items"]]
    assert ids == sorted(ids, reverse=sort.endswith("desc"))
    empty = client.get(
        f"/api/{provider}/statistics/transactions",
        params={"snapshot_timestamp": STAMP.isoformat(), "month": "2020-01"},
    )
    assert empty.json()["items"] == []
    assert empty.json()["total"] == 0


@pytest.mark.parametrize("missing", [False, True])
def test_snapshot_change_requires_refresh(metrics_client, missing):
    client, service = metrics_client
    if missing:
        service.get_cached_snapshot.return_value = None
    else:
        service.get_cached_snapshot.return_value.fetched_at += timedelta(seconds=1)
    response = client.get(
        "/api/blik/statistics/transactions",
        params={"snapshot_timestamp": STAMP.isoformat()},
    )
    assert response.status_code == 409
    service.get_snapshot.assert_not_awaited()


@pytest.mark.parametrize(
    "query", [{"month": "2026-13"}, {"limit": 101}, {"offset": -1}, {"sort": "unknown"}]
)
def test_invalid_queries(metrics_client, query):
    client, _ = metrics_client
    response = client.get(
        "/api/blik/statistics/transactions",
        params={"snapshot_timestamp": STAMP.isoformat(), **query},
    )
    assert response.status_code == 422


def test_details_require_authentication(client):
    response = client.get(
        "/api/blik/statistics/transactions",
        params={"snapshot_timestamp": STAMP.isoformat()},
    )
    assert response.status_code == 401
