from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from services.allegro_stats.manager import AllegroMetricsManager
from services.domain.allegro import (
    AllegroApplyJob,
    AllegroPageMatchCacheEntry,
    AllegroPageRequest,
)
from services.domain.job_base import JobStatus
from services.domain.match_result import MatchResult
from services.matching_cache.backend import Ticket
from services.matching_cache.stores import AllegroMatchStore


class AllegroApplyJobManager:
    def __init__(self) -> None:
        self._jobs: dict[UUID, AllegroApplyJob] = {}

    def create(self, *, secret_id: UUID, total: int) -> AllegroApplyJob:
        job = AllegroApplyJob(
            id=uuid4(),
            secret_id=secret_id,
            total=total,
            status=JobStatus.PENDING,
            started_at=datetime.now(UTC),
        )
        self._jobs[job.id] = job
        return job

    def get(self, job_id: UUID) -> AllegroApplyJob | None:
        return self._jobs.get(job_id)


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
