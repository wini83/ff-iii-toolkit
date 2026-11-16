import asyncio
from datetime import datetime
from uuid import UUID, uuid4

from services.allegro_state_store import (
    AllegroApplyJobManager,
    AllegroStateStore,
)
from services.domain.allegro import (
    AllegroPageMatchCacheEntry,
    AllegroPageRequest,
)
from services.domain.job_base import JobStatus
from services.domain.match_result import MatchResult
from tests.services.test_allegro_application_service import _tx


def test_apply_job_manager_create_and_get():
    manager = AllegroApplyJobManager()
    secret_id = uuid4()

    job = manager.create(secret_id=secret_id, total=5)

    assert job.secret_id == secret_id
    assert job.total == 5
    assert job.status == JobStatus.PENDING
    assert isinstance(job.started_at, datetime)
    assert job.started_at.tzinfo is not None

    fetched = manager.get(job.id)
    assert fetched is job


def test_apply_job_manager_get_missing_returns_none():
    manager = AllegroApplyJobManager()
    assert manager.get(uuid4()) is None


def test_state_store_defaults_are_independent():
    store1 = AllegroStateStore()
    store2 = AllegroStateStore()
    assert store1 is not store2
    assert isinstance(store1, AllegroStateStore)
    assert store1.match_store.backend.buckets == {}
    assert store1.metrics_manager is None


def test_page_cache_is_scoped_per_secret_and_page():
    store = AllegroStateStore()
    secret_id = uuid4()
    other_secret_id = uuid4()
    page_a = AllegroPageRequest(limit=25, offset=0)
    page_b = AllegroPageRequest(limit=25, offset=25)
    match_a = MatchResult(tx=_tx(1), matches=[])
    match_b = MatchResult(tx=_tx(1), matches=[])

    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page_a, login="a", payments=[], matches=[match_a]
            ),
        )
    )
    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page_b, login="a", payments=[], matches=[match_b]
            ),
        )
    )
    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=other_secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page_a, login="b", payments=[], matches=[]
            ),
        )
    )

    assert asyncio.run(
        store.get_page_matches(owner=OWNER, secret_id=secret_id, page=page_a)
    ) == [match_a]
    assert asyncio.run(
        store.get_page_matches(owner=OWNER, secret_id=secret_id, page=page_b)
    ) == [match_b]
    assert (
        asyncio.run(
            store.get_page_matches(owner=OWNER, secret_id=other_secret_id, page=page_a)
        )
        == []
    )
    assert asyncio.run(store.get_all_matches(owner=OWNER, secret_id=secret_id)) == [
        match_a,
        match_b,
    ]


def test_invalidate_page_removes_only_selected_page():
    store = AllegroStateStore()
    secret_id = uuid4()
    page_a = AllegroPageRequest(limit=25, offset=0)
    page_b = AllegroPageRequest(limit=25, offset=25)
    match_a = MatchResult(tx=_tx(1), matches=[])
    match_b = MatchResult(tx=_tx(1), matches=[])

    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page_a, login="a", payments=[], matches=[match_a]
            ),
        )
    )
    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page_b, login="a", payments=[], matches=[match_b]
            ),
        )
    )

    assert (
        asyncio.run(
            store.invalidate_page(owner=OWNER, secret_id=secret_id, page=page_a)
        )
        is True
    )
    assert (
        asyncio.run(
            store.get_page_matches(owner=OWNER, secret_id=secret_id, page=page_a)
        )
        is None
    )
    assert asyncio.run(
        store.get_page_matches(owner=OWNER, secret_id=secret_id, page=page_b)
    ) == [match_b]
    assert asyncio.run(store.get_all_matches(owner=OWNER, secret_id=secret_id)) == [
        match_b
    ]


def test_invalidate_secret_and_all():
    store = AllegroStateStore()
    secret_a = uuid4()
    secret_b = uuid4()
    page = AllegroPageRequest(limit=25, offset=0)

    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_a,
            entry=AllegroPageMatchCacheEntry(
                page=page, login="a", payments=[], matches=[]
            ),
        )
    )
    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_b,
            entry=AllegroPageMatchCacheEntry(
                page=page, login="b", payments=[], matches=[]
            ),
        )
    )

    assert asyncio.run(store.invalidate_secret(owner=OWNER, secret_id=secret_a)) is True
    assert asyncio.run(store.get_all_matches(owner=OWNER, secret_id=secret_a)) == []
    assert (
        asyncio.run(store.get_page_matches(owner=OWNER, secret_id=secret_b, page=page))
        == []
    )

    asyncio.run(store.invalidate_all())
    assert store.match_store.backend.buckets == {}


def test_invalidate_page_returns_false_for_missing_secret_or_page():
    store = AllegroStateStore()
    secret_id = uuid4()
    page = AllegroPageRequest(limit=25, offset=0)

    assert (
        asyncio.run(store.invalidate_page(owner=OWNER, secret_id=secret_id, page=page))
        is False
    )

    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page, login="a", payments=[], matches=[]
            ),
        )
    )

    assert (
        asyncio.run(
            store.invalidate_page(
                owner=OWNER,
                secret_id=secret_id,
                page=AllegroPageRequest(limit=25, offset=25),
            )
        )
        is False
    )


def test_invalidate_page_removes_secret_bucket_when_last_page_removed():
    store = AllegroStateStore()
    secret_id = uuid4()
    page = AllegroPageRequest(limit=25, offset=0)
    asyncio.run(
        store.put_page_matches(
            owner=OWNER,
            secret_id=secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page, login="a", payments=[], matches=[]
            ),
        )
    )

    assert (
        asyncio.run(store.invalidate_page(owner=OWNER, secret_id=secret_id, page=page))
        is True
    )
    assert asyncio.run(store.get_all_matches(owner=OWNER, secret_id=secret_id)) == []


def test_invalidate_secret_returns_false_when_missing():
    store = AllegroStateStore()
    assert asyncio.run(store.invalidate_secret(owner=OWNER, secret_id=uuid4())) is False


OWNER = UUID(int=1)
