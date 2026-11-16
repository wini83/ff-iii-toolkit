import asyncio
import hashlib
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from api.deps_runtime import get_blik_application_runtime
from services.allegro_application_service import AllegroApplicationService
from services.allegro_state_store import AllegroStateStore
from services.blik_application_service import BlikApplicationService
from services.blik_state_store import BlikStateStore
from services.domain.user_secrets import SecretType
from services.exceptions import (
    FileNotFound,
    InvalidSecretId,
    MatchesNotComputed,
    SecretNotAccessible,
)
from services.firefly_base_service import FireflyServiceError
from services.firefly_enrichment_service import FireflyEnrichmentService
from services.guards import require_active_user
from services.matching_cache.backend import MemoryMatchBackend
from services.matching_cache.stores import AllegroMatchStore
from services.tx_application_service import TxApplicationService
from services.user_secrets_service import UserSecretsService
from tests.services.test_matching_cache import blik_matches, page_entry
from utils.encoding import encode_base64url


def secret_service(owner, secret_id):
    obj = SimpleNamespace(
        id=secret_id,
        user_id=owner,
        type=SecretType.ALLEGRO,
        ciphertext=b"encrypted synthetic credential",
        secret_nonce=b"nonce",
    )
    repo = MagicMock()
    repo.get_by_id.return_value = obj
    service = UserSecretsService(
        secret_repo=repo,
        audit_repo=MagicMock(),
        vault_service=MagicMock(),
        crypto_service=MagicMock(),
    )
    return service, obj


def allegro_service(owner, secret, backend):
    secrets, obj = secret_service(owner, secret)
    service = AllegroApplicationService(
        secrets_service=secrets,
        enrichment_service=MagicMock(),
        metrics_provider=MagicMock(),
        allegro_service=MagicMock(),
        state_store=AllegroStateStore(match_store=AllegroMatchStore(backend)),
        filter_desc_allegro="allegro",
    )
    return service, obj


def test_allegro_apply_clear_and_job_lookup_authorize_secret_owner():
    async def check():
        owner, outsider, secret = uuid4(), uuid4(), uuid4()
        b = MemoryMatchBackend()
        service, obj = allegro_service(owner, secret, b)
        revision = service._matching_revision(user_id=owner, secret_id=secret)
        await service.state_store.put_page_matches(
            owner=owner, secret_id=secret, revision=revision, entry=page_entry()
        )
        for method, kwargs in [
            (service.start_apply_job, {"decisions": []}),
            (service.start_auto_apply_single_matches, {}),
            (service.clear_cached_page, {"page": page_entry().page}),
            (service.clear_cached_secret, {}),
        ]:
            with pytest.raises(InvalidSecretId):
                await method(user_id=outsider, secret_id=secret, **kwargs)
        assert await service.state_store.get_all_matches(
            owner=owner, secret_id=secret, revision=revision
        )
        job = await service.state_store.job_manager.create(
            owner=owner, secret_id=secret, total=0
        )
        assert await service.get_apply_job(user_id=outsider, job_id=job.id) is None
        assert await service.get_apply_job(user_id=owner, job_id=job.id) == job
        await service.state_store.job_manager.abort_start(job)
        obj.ciphertext = b"rotated synthetic credential"
        with pytest.raises(MatchesNotComputed):
            await service.start_apply_job(user_id=owner, secret_id=secret, decisions=[])
        service.secrets_service.secret_repo.get_by_id.return_value = None
        with pytest.raises(InvalidSecretId):
            await service.start_apply_job(user_id=owner, secret_id=secret, decisions=[])

    asyncio.run(check())


def test_secret_revision_uses_ownership_without_decrypting_token():
    owner, secret = uuid4(), uuid4()
    service, obj = secret_service(owner, secret)
    first = service.matching_revision(user_id=owner, secret_id=secret)
    assert first == hashlib.sha256(obj.ciphertext + obj.secret_nonce).hexdigest()
    with pytest.raises(SecretNotAccessible):
        service.matching_revision(user_id=uuid4(), secret_id=secret)
    service.crypto_service.decrypt_secret.assert_not_called()
    service.vault_service.require_user_key.assert_not_called()
    obj.secret_nonce = b"new nonce"
    assert service.matching_revision(user_id=owner, secret_id=secret) != first


def test_allegro_inflight_preview_cannot_repopulate_cleared_cache():
    async def check():
        owner, secret = uuid4(), uuid4()
        b = MemoryMatchBackend()
        service, _ = allegro_service(owner, secret, b)
        service.fetch_allegro_data = MagicMock(
            return_value=SimpleNamespace(payments=[])
        )

        async def match(**kwargs):
            await service.clear_cached_secret(user_id=owner, secret_id=secret)
            return [], []

        service._match_page = match
        with pytest.raises(MatchesNotComputed):
            await service.preview_matches(
                user_id=owner, secret_id=secret, vault_session_id=None
            )
        revision = service._matching_revision(user_id=owner, secret_id=secret)
        assert (
            await service.state_store.get_all_matches(
                owner=owner, secret_id=secret, revision=revision
            )
            == []
        )

    asyncio.run(check())


def test_allegro_inflight_preview_rejects_rotated_credentials():
    async def check():
        owner, secret = uuid4(), uuid4()
        service, obj = allegro_service(owner, secret, MemoryMatchBackend())
        service.fetch_allegro_data = MagicMock(
            return_value=SimpleNamespace(payments=[])
        )

        async def match(**kwargs):
            obj.ciphertext = b"rotated"
            return [], []

        service._match_page = match
        with pytest.raises(MatchesNotComputed):
            await service.preview_matches(
                user_id=owner, secret_id=secret, vault_session_id=None
            )

    asyncio.run(check())


def test_blik_file_owner_content_revision_and_job_owner(tmp_path):
    async def check():
        owner, outsider = uuid4(), uuid4()
        path = tmp_path / f"blik-{owner.hex}-synthetic.csv"
        path.write_bytes(b"synthetic CSV")
        file_id = encode_base64url(path.stem)
        service = BlikApplicationService(
            enrichment_service=MagicMock(),
            metrics_provider=MagicMock(),
            state_store=BlikStateStore(),
        )
        service._resolve_csv_path = lambda _: str(path)
        revision = service._file_revision(str(path))
        await service.state_store.put_matches(
            owner=owner, file_id=file_id, revision=revision, matches=blik_matches()
        )
        for method, kwargs in [
            (service.preview_csv, {}),
            (service.preview_matches, {}),
            (service.start_apply_job, {"decisions": []}),
            (service.start_auto_apply_single_matches, {}),
        ]:
            with pytest.raises(FileNotFound):
                await method(user_id=outsider, encoded_id=file_id, **kwargs)
        assert await service.state_store.get_matches(
            owner=owner, file_id=file_id, revision=revision
        )
        job = await service.state_store.job_manager.create(
            owner=owner, file_id=file_id, total=0
        )
        assert await service.get_apply_job(user_id=outsider, job_id=job.id) is None
        assert await service.get_apply_job(user_id=owner, job_id=job.id) == job
        await service.state_store.job_manager.abort_start(job)
        path.write_bytes(b"changed CSV")
        with pytest.raises(MatchesNotComputed):
            await service.start_apply_job(
                user_id=owner, encoded_id=file_id, decisions=[]
            )

    asyncio.run(check())


def test_blik_cross_user_apply_returns_404_instead_of_500(client, tmp_path):
    owner = uuid4()
    service = BlikApplicationService(
        enrichment_service=MagicMock(),
        metrics_provider=MagicMock(),
        state_store=BlikStateStore(),
    )
    client.app.dependency_overrides[get_blik_application_runtime] = lambda: service
    client.app.dependency_overrides[require_active_user] = lambda: uuid4()
    file_id = encode_base64url(f"blik-{owner.hex}-synthetic")
    assert (
        client.post(
            f"/api/blik_files/{file_id}/apply", json={"decisions": []}
        ).status_code
        == 404
    )
    assert client.post(f"/api/blik_files/{file_id}/apply/auto").status_code == 404


def test_transaction_update_invalidates_other_feature_and_pending_writers():
    async def check():
        b = MemoryMatchBackend()
        ticket = await b.begin("allegro")
        await b.put("allegro", "page", ticket, "cached")
        tx_service = MagicMock()

        async def change(**kwargs):
            assert await b.values("allegro") == []
            # Matching begun during the external mutation also becomes stale.
            during = await b.begin("blik")
            await b.put("blik", "matches", during, "intermediate")

        tx_service.apply_category_by_id = change
        service = TxApplicationService(
            tx_service=tx_service, metrics_provider=MagicMock(), matching_backend=b
        )
        await service.apply_category(tx_id=1, category_id=2)
        assert await b.values("blik") == []
        assert not await b.put("allegro", "page", ticket, "late")

    asyncio.run(check())


def test_apply_enrichment_rechecks_live_transaction_and_preserves_newer_data():
    cached = blik_matches()[0].tx
    current = replace(cached, tags={"new-tag"}, notes="A newer note")
    evidence = blik_matches()[0].matches[0]
    service = FireflyEnrichmentService(MagicMock())
    service._get_current_transaction = AsyncMock(return_value=current)
    service.update_transaction = AsyncMock()
    asyncio.run(service.apply_match(cached, evidence))
    service.update_transaction.assert_awaited_once()
    (tx,) = service.update_transaction.call_args.args
    update = service.update_transaction.call_args.kwargs["payload"]
    assert tx is current
    assert "new-tag" in update.tags
    assert "A newer note" in update.notes


@pytest.mark.parametrize("change", ["amount", "date", "currency", "processed"])
def test_apply_enrichment_refuses_changed_or_newly_processed_transactions(change):
    cached = blik_matches()[0].tx
    current = replace(cached)
    if change == "amount":
        current.amount += 1
    elif change == "date":
        from datetime import timedelta

        current.date += timedelta(days=1)
    elif change == "currency":
        current.currency = replace(current.currency, code="EUR")
    else:
        current.tags = {"blik_done"}
    service = FireflyEnrichmentService(MagicMock())
    service._get_current_transaction = AsyncMock(return_value=current)
    service.update_transaction = AsyncMock()
    with pytest.raises(FireflyServiceError):
        asyncio.run(service.apply_match(cached, blik_matches()[0].matches[0]))
    service.update_transaction.assert_not_awaited()


def test_live_transaction_verification_failure_never_applies_cached_data():
    from ff_iii_luciferin.api import FireflyAPIError

    client = MagicMock()
    client.get_transaction = AsyncMock(
        side_effect=FireflyAPIError("sensitive upstream details", status_code=404)
    )
    service = FireflyEnrichmentService(client)
    service.update_transaction = AsyncMock()
    with pytest.raises(FireflyServiceError) as exc:
        asyncio.run(
            service.apply_match(blik_matches()[0].tx, blik_matches()[0].matches[0])
        )
    assert "sensitive" not in str(exc.value)
    service.update_transaction.assert_not_awaited()


def test_live_transaction_verification_maps_current_firefly_response():
    tx = blik_matches()[0].tx
    client = MagicMock()
    client.get_transaction = AsyncMock(
        return_value=SimpleNamespace(
            id=tx.id,
            date=tx.date,
            amount=tx.amount,
            type=SimpleNamespace(value=tx.type.value),
            description=tx.description,
            tags=list(tx.tags),
            notes=tx.notes,
            category=None,
            currency=tx.currency,
            fx=None,
        )
    )
    service = FireflyEnrichmentService(client)
    assert asyncio.run(service._get_current_transaction(tx.id)) == tx
    client.get_transaction.assert_awaited_once_with(tx.id)


def test_application_restart_reuses_redis_matching_results(monkeypatch):
    import os
    from functools import partial

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from main import create_app
    from services.matching_cache.backend import RedisMatchBackend
    from services.snapshot.store import InMemorySnapshotStore
    from settings import settings

    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for matching application restart test")
    monkeypatch.setattr(settings, "REDIS_URL", SecretStr(url))
    owner = uuid4()
    kwargs = {"owner": owner, "file_id": "synthetic-restart", "revision": "digest"}
    for mode in ["write", "read"]:
        with TestClient(create_app(snapshot_store=InMemorySnapshotStore())) as client:
            b = client.app.state.matching_backend
            assert isinstance(b, RedisMatchBackend)
            assert b.redis is client.app.state.redis
            store = client.app.state.blik_runtime_state.match_store
            assert store.backend is b
            assert client.app.state.allegro_runtime_state.match_store.backend is b
            if mode == "write":
                assert client.portal.call(
                    partial(store.put, **kwargs, matches=blik_matches())
                )
            else:
                try:
                    assert (
                        client.portal.call(partial(store.get, **kwargs))
                        == blik_matches()
                    )
                finally:
                    client.portal.call(b.invalidate, store.scope(**kwargs))


@pytest.mark.parametrize("feature", ["blik", "allegro"])
@pytest.mark.parametrize("cache_failure", [False, True])
def test_apply_finishes_only_after_cache_invalidation(feature, cache_failure):
    from services.domain.job_base import JobStatus
    from services.matching_cache.backend import MatchCacheUnavailable

    async def check():
        entered = asyncio.Event()
        release = asyncio.Event()

        async def invalidate():
            entered.set()
            await release.wait()
            if cache_failure:
                raise MatchCacheUnavailable("synthetic failure")

        service_type = (
            BlikApplicationService if feature == "blik" else AllegroApplicationService
        )
        service = object.__new__(service_type)
        service.state_store = (
            BlikStateStore() if feature == "blik" else AllegroStateStore()
        )
        service.state_store.match_store.backend.invalidate_all = invalidate
        resource = (
            {"file_id": "synthetic"} if feature == "blik" else {"secret_id": uuid4()}
        )
        job = await service.state_store.job_manager.create(
            owner=uuid4(), total=0, **resource
        )
        task = asyncio.create_task(
            service._run_apply_job(job=job, decisions=[], matches=[])
        )
        await asyncio.wait_for(entered.wait(), 2)
        assert job.status == JobStatus.RUNNING
        assert job.finished_at is None
        release.set()
        await task
        assert job.status == (JobStatus.FAILED if cache_failure else JobStatus.DONE)
        assert job.finished_at is not None

    asyncio.run(check())
