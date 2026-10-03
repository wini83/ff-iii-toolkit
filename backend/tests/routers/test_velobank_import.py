"""Use synthetic documents only; exercise authentication, isolation and export."""

import csv
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO, StringIO
from uuid import uuid4
from zipfile import ZipFile

import pytest

from api.deps_runtime import get_velobank_preview_store
from api.routers import velobank_import as router
from api.routers.auth import create_access_token
from services.db.models import VeloBankProfileORM
from services.db.repository import UserRepository
from services.velobank_import.parser import VeloBankStatement, VeloBankTransaction
from services.velobank_import.session import VeloBankPreviewStore
from tests.services.velobank_import.test_velobank import (
    ACCOUNT,
    OTHER_ACCOUNT,
    make_pdf,
    row,
)

ROOT = "/api/tools/velobank"
IBAN = "PL" + ACCOUNT
OTHER_IBAN = "PL" + OTHER_ACCOUNT


@pytest.fixture
def actor(client, db):
    user = UserRepository(db).create(
        username="velo", password_hash="hashed", is_superuser=False
    )
    store = VeloBankPreviewStore()
    client.app.dependency_overrides[get_velobank_preview_store] = lambda: store
    return user, {"Authorization": f"Bearer {create_access_token(str(user.id))}"}, store


def upload(client, headers, tmp_path):
    path = tmp_path / "synthetic.pdf"
    make_pdf(path, [[row("Shop", "-12,34 PLN"), row("Repayment", "20,00 PLN")]])
    return client.post(
        ROOT + "/upload",
        headers=headers,
        files={"file": (path.name, path.read_bytes(), "application/pdf")},
    )


def test_upload_preserves_dates_credits_totals_and_decimal_strings(
    client, actor, tmp_path
):
    _, headers, _ = actor
    response = upload(client, headers, tmp_path)
    assert response.status_code == 200
    data = response.json()
    assert data["record_count"] == 2
    assert data["preview"][0]["amount"] == "-12.34"
    assert data["preview"][0]["date"] == "2026-09-01"
    assert data["preview"][0]["booking_date"] == "2026-09-02"
    assert data["totals"] == [
        {"currency": "PLN", "debits": "12.34", "credits": "20.00"}
    ]
    assert data["account_iban"] == IBAN
    assert response.headers["cache-control"] == "no-store"
    assert (
        client.get(ROOT + "/files/" + data["file_id"], headers=headers).status_code
        == 200
    )


def test_mapping_profile_survives_new_preview_and_is_user_owned(
    client, db, actor, tmp_path
):
    user, headers, _ = actor
    payload = {"accounts": {ACCOUNT: " Card ", OTHER_IBAN: "Main"}}
    saved = client.put(ROOT + "/mappings", json=payload, headers=headers)
    assert saved.json() == {"accounts": {IBAN: "Card", OTHER_IBAN: "Main"}}
    assert db.get(VeloBankProfileORM, user.id).accounts_json[IBAN] == "Card"
    assert upload(client, headers, tmp_path).json()["account_name"] == "Card"
    another = UserRepository(db).create(
        username="other", password_hash="hashed", is_superuser=False
    )
    other_headers = {"Authorization": f"Bearer {create_access_token(str(another.id))}"}
    assert client.get(ROOT + "/mappings", headers=other_headers).json() == {
        "accounts": {}
    }
    client.put(ROOT + "/mappings", json={"accounts": {}}, headers=headers)
    assert client.get(ROOT + "/mappings", headers=headers).json() == {"accounts": {}}


@pytest.mark.parametrize(
    "accounts", [{"invalid": "Card"}, {IBAN: " "}, {IBAN: "Card", ACCOUNT: "Other"}]
)
def test_mapping_validation(client, actor, accounts):
    _, headers, _ = actor
    assert (
        client.put(
            ROOT + "/mappings", headers=headers, json={"accounts": accounts}
        ).status_code
        == 422
    )


def test_csv_zip_and_preview_share_ids_mapping_and_legitimate_duplicates(client, actor):
    user, headers, store = actor
    tx = VeloBankTransaction(
        date(2026, 9, 1),
        date(2026, 9, 2),
        "Operacja kartą test na kwotę 12,34 PLN w Shop; test",
        Decimal("-12.34"),
        "PLN",
    )
    credit = VeloBankTransaction(
        tx.date,
        tx.booking_date,
        f"Przelew z rachunku: {OTHER_ACCOUNT}, Nadawca: Test Person",
        Decimal("20.00"),
        "PLN",
    )
    entry = store.create(user.id, VeloBankStatement(ACCOUNT, [tx, tx, credit]))
    url = f"{ROOT}/files/{entry.id}"
    payload = {"accounts": {IBAN: "Card", OTHER_IBAN: "Main"}}
    preview = client.post(url + "/preview", json=payload, headers=headers).json()
    assert len({r["id"] for r in preview["preview"]}) == 3
    assert preview["preview"][2]["source_account"] == "Main"
    assert preview["preview"][2]["destination_account"] == "Card"
    assert client.get(ROOT + "/mappings", headers=headers).json() == {"accounts": {}}
    response = client.post(url + "/export-csv", json=payload, headers=headers)
    assert response.status_code == 200
    csv_rows = list(csv.DictReader(StringIO(response.text), delimiter=";"))
    assert [r["External ID"] for r in csv_rows] == [r["id"] for r in preview["preview"]]
    assert csv_rows[0]["Payee"] == "Shop; test"
    zipped = client.post(
        url + "/export-csv?chunk_size=1", json=payload, headers=headers
    )
    with ZipFile(BytesIO(zipped.content)) as archive:
        zip_rows = [
            r
            for name in archive.namelist()
            for r in csv.DictReader(
                StringIO(archive.read(name).decode()), delimiter=";"
            )
        ]
    assert zip_rows == csv_rows
    assert (
        client.post(
            url + "/export-csv", headers=headers, json={"accounts": {}}
        ).status_code
        == 400
    )


def test_other_user_cannot_read_configure_export_or_delete_preview(
    client, db, actor, tmp_path
):
    _, headers, _ = actor
    key = upload(client, headers, tmp_path).json()["file_id"]
    user = UserRepository(db).create(
        username="outsider", password_hash="hashed", is_superuser=False
    )
    other = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    url = ROOT + "/files/" + key
    assert client.get(url, headers=other).status_code == 404
    assert (
        client.post(url + "/preview", headers=other, json={"accounts": {}}).status_code
        == 404
    )
    assert (
        client.post(
            url + "/export-csv", headers=other, json={"accounts": {IBAN: "Card"}}
        ).status_code
        == 404
    )
    assert client.delete(url, headers=other).status_code == 404
    assert client.get(url, headers=headers).status_code == 200


def test_expiry_and_discard_prevent_further_exports(client, actor, tmp_path):
    _, headers, store = actor
    key = upload(client, headers, tmp_path).json()["file_id"]
    url = ROOT + "/files/" + key
    assert client.delete(url, headers=headers).status_code == 204
    assert client.get(url, headers=headers).status_code == 404
    store.ttl_seconds = -1
    key = upload(client, headers, tmp_path).json()["file_id"]
    assert (
        client.post(
            ROOT + "/files/" + key + "/export-csv",
            headers=headers,
            json={"accounts": {IBAN: "Card"}},
        ).status_code
        == 404
    )
    assert not store._entries


def test_preview_store_limits_and_purge():
    store = VeloBankPreviewStore()
    owner = uuid4()
    first = store.create(owner, VeloBankStatement(ACCOUNT, []))
    for _ in range(5):
        store.create(owner, VeloBankStatement(ACCOUNT, []))
    assert len(store._entries) == 5
    assert first.id not in store._entries
    from dataclasses import replace

    store._entries = {
        key: replace(entry, expires_at=entry.expires_at - timedelta(hours=1))
        for key, entry in store._entries.items()
    }
    store.purge_expired()
    assert not store._entries


def test_upload_rejects_invalid_pdf_and_limit_without_creating_preview(
    client, actor, monkeypatch
):
    _, headers, store = actor
    for name, content, expected in [
        ("x.txt", b"text", 400),
        ("x.pdf", b"text", 400),
        ("x.pdf", b"%PDF-invalid", 400),
    ]:
        assert (
            client.post(
                ROOT + "/upload", headers=headers, files={"file": (name, content)}
            ).status_code
            == expected
        )
    monkeypatch.setattr(router, "MAX_UPLOAD_BYTES", 5)
    assert (
        client.post(
            ROOT + "/upload", headers=headers, files={"file": ("x.pdf", b"%PDF-123")}
        ).status_code
        == 413
    )
    assert not store._entries


def test_all_import_routes_require_auth(client):
    for path in ["/mappings", "/accounts", "/files/" + str(uuid4())]:
        assert client.get(ROOT + path).status_code == 401
    assert (
        client.post(ROOT + "/upload", files={"file": ("x.pdf", b"%PDF-")}).status_code
        == 401
    )


def test_accounts_success_and_safe_upstream_failure(client, actor, monkeypatch):
    _, headers, _ = actor
    monkeypatch.setattr(router.settings, "FIREFLY_URL", "https://firefly.example")
    monkeypatch.setattr(router.settings, "FIREFLY_TOKEN", "test-token")

    async def accounts(*args):
        return [{"id": "1", "name": "Card", "iban": IBAN}]

    monkeypatch.setattr(router, "fetch_asset_accounts", accounts)
    assert client.get(ROOT + "/accounts", headers=headers).json()[0]["name"] == "Card"

    async def broken(*args):
        import httpx

        raise httpx.ConnectError("private upstream URL and token")

    monkeypatch.setattr(router, "fetch_asset_accounts", broken)
    response = client.get(ROOT + "/accounts", headers=headers)
    assert response.status_code == 502
    assert "private upstream" not in response.text
