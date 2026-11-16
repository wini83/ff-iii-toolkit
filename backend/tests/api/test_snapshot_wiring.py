from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from api.deps_services import get_firefly_enrichment_service
from services.domain.metrics import FetchMetrics
from services.guards import require_active_user


def test_all_snapshot_consumers_use_the_app_store(client, monkeypatch):
    firefly = MagicMock()
    firefly.fetch_transactions_with_metrics = AsyncMock(
        return_value=([], FetchMetrics(0, 1, 0, 0))
    )
    monkeypatch.setattr("api.deps_services.get_firefly_base_service", lambda: firefly)
    monkeypatch.setattr("api.deps_runtime.get_firefly_tx_service", MagicMock())
    monkeypatch.setattr("api.deps_runtime.get_firefly_enrichment_service", MagicMock())
    client.app.dependency_overrides[require_active_user] = lambda: uuid4()
    client.app.dependency_overrides[get_firefly_enrichment_service] = lambda: (
        MagicMock()
    )

    assert client.get("/api/tx/statistics").status_code == 200
    service = client.app.state.transaction_snapshot_service
    client.portal.call(service.get_snapshot)
    assert service.store is client.app.state.snapshot_store
    assert (
        client.app.state.tx_application_runtime.tx_metrics_manager._provider.snapshot_service
        is service
    )

    assert client.get("/api/blik_files/statistics_v2").status_code == 200
    assert (
        client.app.state.blik_application_runtime.metrics_provider.snapshot_service
        is service
    )

    assert client.get("/api/allegro/statistics").status_code == 200
    assert (
        client.app.state.allegro_runtime_state.metrics_manager._provider.snapshot_service
        is service
    )

    # An empty snapshot gives a normal 404, rather than a dependency wiring error.
    assert client.get("/api/tx/42/category-suggestions").status_code == 404
    firefly.fetch_transactions_with_metrics.assert_awaited_once()

    backend = client.app.state.matching_backend
    assert (
        client.app.state.blik_application_runtime.state_store.match_store.backend
        is backend
    )
    assert client.app.state.allegro_runtime_state.match_store.backend is backend
    assert client.app.state.tx_application_runtime.matching_backend is backend
