from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from services.allegro_stats.manager import AllegroMetricsManager
from services.domain.allegro import (
    AllegroApplyJob,
    AllegroPageMatchCacheEntry,
    AllegroPageRequest,
)
from services.domain.match_result import MatchResult
from services.jobs.apply import ApplyJobManager
from services.jobs.runtime import JobRuntime
from services.matching_cache.backend import Ticket
from services.matching_cache.stores import AllegroMatchStore


class AllegroApplyJobManager(ApplyJobManager[AllegroApplyJob]):
    def __init__(self, runtime: JobRuntime | None = None):
        super().__init__(AllegroApplyJob, "allegro-apply", runtime)


@dataclass
class AllegroStateStore:
    match_store: AllegroMatchStore = field(default_factory=AllegroMatchStore)
    job_manager: AllegroApplyJobManager = field(default_factory=AllegroApplyJobManager)
    metrics_manager: AllegroMetricsManager | None = None

    async def put_page_matches(
        self,
        *,
        owner: UUID,
        secret_id: UUID,
        entry: AllegroPageMatchCacheEntry,
        revision: str = "revision",
        ticket: Ticket | None = None,
    ) -> bool:
        return await self.match_store.put(
            owner=owner,
            secret_id=secret_id,
            revision=revision,
            entry=entry,
            ticket=ticket,
        )

    async def get_page_matches(
        self,
        *,
        owner: UUID,
        secret_id: UUID,
        page: AllegroPageRequest,
        revision: str = "revision",
    ) -> list[MatchResult] | None:
        return await self.match_store.get(
            owner=owner, secret_id=secret_id, revision=revision, page=page
        )

    async def get_all_matches(
        self,
        *,
        owner: UUID,
        secret_id: UUID,
        revision: str = "revision",
        consume: bool = False,
    ) -> list[MatchResult]:
        return await self.match_store.get_all(
            owner=owner, secret_id=secret_id, revision=revision, consume=consume
        )

    async def invalidate_page(
        self,
        *,
        owner: UUID,
        secret_id: UUID,
        page: AllegroPageRequest,
        revision: str = "revision",
    ) -> bool:
        return await self.match_store.invalidate(
            owner=owner, secret_id=secret_id, revision=revision, page=page
        )

    async def invalidate_secret(
        self, *, owner: UUID, secret_id: UUID, revision: str = "revision"
    ) -> bool:
        return await self.match_store.invalidate(
            owner=owner, secret_id=secret_id, revision=revision
        )

    async def invalidate_all(self) -> None:
        await self.match_store.invalidate_all()
