from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from services.domain.blik import BlikApplyJob
from services.domain.job_base import JobStatus
from services.domain.match_result import MatchResult
from services.matching_cache.backend import Ticket
from services.matching_cache.stores import BlikMatchStore


class BlikApplyJobManager:
    def __init__(self) -> None:
        self._jobs: dict[UUID, BlikApplyJob] = {}

    def create(self, *, file_id: str, total: int) -> BlikApplyJob:
        job = BlikApplyJob(
            id=uuid4(),
            file_id=file_id,
            total=total,
            status=JobStatus.PENDING,
            started_at=datetime.now(UTC),
        )
        self._jobs[job.id] = job
        return job

    def get(self, job_id: UUID) -> BlikApplyJob | None:
        return self._jobs.get(job_id)


@dataclass
class BlikStateStore:
    match_store: BlikMatchStore = field(default_factory=BlikMatchStore)
    job_manager: BlikApplyJobManager = field(default_factory=BlikApplyJobManager)

    async def put_matches(
        self,
        *,
        owner: UUID,
        file_id: str,
        matches: list[MatchResult],
        revision: str = "digest",
        ticket: Ticket | None = None,
    ) -> bool:
        return await self.match_store.put(
            owner=owner,
            file_id=file_id,
            revision=revision,
            matches=matches,
            ticket=ticket,
        )

    async def get_matches(
        self,
        *,
        owner: UUID,
        file_id: str,
        revision: str = "digest",
        consume: bool = False,
    ) -> list[MatchResult]:
        return (
            await self.match_store.get(
                owner=owner, file_id=file_id, revision=revision, consume=consume
            )
            or []
        )
