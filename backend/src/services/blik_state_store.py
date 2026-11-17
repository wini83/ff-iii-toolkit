from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from services.domain.blik import BlikApplyJob
from services.domain.match_result import MatchResult
from services.jobs.apply import ApplyJobManager
from services.jobs.runtime import JobRuntime
from services.matching_cache.backend import Ticket
from services.matching_cache.stores import BlikMatchStore


class BlikApplyJobManager(ApplyJobManager[BlikApplyJob]):
    def __init__(self, runtime: JobRuntime | None = None):
        super().__init__(BlikApplyJob, "blik-apply", runtime)


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
