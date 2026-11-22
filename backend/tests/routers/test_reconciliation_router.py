from unittest.mock import AsyncMock, MagicMock

import pytest

from api.deps_services import get_firefly_client
from services.guards import require_active_user
from tests.services.test_reconciliation import DAY, statement, transaction


@pytest.fixture
def reconciliation_client(client, monkeypatch):
    from types import SimpleNamespace

    firefly = MagicMock()
    firefly.get_transaction = AsyncMock(return_value=transaction())
    firefly.update_transaction = AsyncMock()

    async def fetch(*args, **kwargs):
        return [transaction()], SimpleNamespace(multipart=0, invalid=0)

    monkeypatch.setattr(
        "services.reconciliation.service.fetch_transactions_with_stats", fetch
    )
    client.app.dependency_overrides[require_active_user] = lambda: "user"
    client.app.dependency_overrides[get_firefly_client] = lambda: firefly
    return client, firefly


def fields(**changes):
    return {
        "account_id": "1",
        "date_from": DAY.isoformat(),
        "date_to": DAY.isoformat(),
        "tolerance_days": "0",
    } | changes


def upload(client, path="preview", **changes):
    return client.post(
        f"/api/reconciliation/{path}",
        data=fields(**changes),
        files={"file": ("bank.csv", statement(), "text/csv")},
    )


def test_preview_requires_authentication(client):
    assert upload(client).status_code == 401
    assert (
        upload(client, "apply", revision="a" * 64, selected_ids="[1]").status_code
        == 401
    )


def test_preview_and_invalid_selection(reconciliation_client):
    client, firefly = reconciliation_client
    response = upload(client)
    assert response.status_code == 200
    preview = response.json()
    assert preview["rows"][0]["status"] == "matched"
    invalid = upload(
        client, "apply", revision=preview["revision"], selected_ids="[999]"
    )
    assert invalid.status_code == 422
    firefly.update_transaction.assert_not_awaited()


def test_multipart_preview_then_apply_marks_and_verifies(reconciliation_client):
    client, firefly = reconciliation_client
    preview = upload(client).json()
    firefly.get_transaction.side_effect = [
        transaction(),
        transaction(),
        transaction(reconciled=True),
    ]
    response = upload(client, "apply", revision=preview["revision"], selected_ids="[1]")
    assert response.status_code == 200
    assert response.json() == {"outcomes": [{"id": 1, "status": "reconciled"}]}
    args, kwargs = firefly.update_transaction.await_args
    assert args[0] == 1 and args[1].reconciled is True
    assert kwargs == {"apply_rules": False, "fire_webhooks": False}


@pytest.mark.parametrize(
    "changes",
    [
        {"date_from": "2026-11-20"},
        {"account_id": "0"},
        {"tolerance_days": "3"},
        {"date_from": "2020-01-01"},
    ],
)
def test_bad_filters(reconciliation_client, changes):
    assert upload(reconciliation_client[0], **changes).status_code == 422


def test_stale_revision_and_bounded_upload(reconciliation_client):
    client, firefly = reconciliation_client
    assert (
        upload(client, "apply", revision="0" * 64, selected_ids="[1]").status_code
        == 409
    )
    response = client.post(
        "/api/reconciliation/preview",
        data=fields(),
        files={"file": ("big.csv", b"a" * (2 * 1024 * 1024 + 1))},
    )
    assert response.status_code == 413
    firefly.update_transaction.assert_not_awaited()
