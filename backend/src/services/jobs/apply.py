"""Typed apply state and durable progress checkpoints."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from services.domain.allegro import AllegroApplyJob
from services.domain.allegro import ApplyOutcome as AllegroOutcome
from services.domain.blik import ApplyOutcome as BlikOutcome
from services.domain.blik import BlikApplyJob
from services.domain.job_base import JobStatus
from services.jobs.runtime import JobRuntime, fail_record
from services.jobs.store import (
    JobBusy,
    JobLeaseLost,
    JobStoreUnavailable,
    MemoryJobStore,
    Record,
)


class ApplyJobManager[T: (BlikApplyJob, AllegroApplyJob)]:
    def __init__(self, model: type[T], kind: str, runtime: JobRuntime | None = None):
        self.model, self.kind = model, kind
        self.runtime = runtime if runtime is not None else JobRuntime(MemoryJobStore())
        self.adapter = TypeAdapter(model)
        # Local execution handles only, never used to serve polling requests.
        self.executions: dict[str, Record] = {}

    async def create(self, *, owner: UUID, total: int, **resource) -> T:
        job = self.model(id=uuid4(), total=total, status=JobStatus.PENDING, **resource)
        record, claimed = await self.runtime.store.claim(
            Record(
                id=str(job.id),
                owner=str(owner),
                kind=self.kind,
                scope="ledger:apply",
                data=self.adapter.dump_python(job, mode="json"),
            )
        )
        if not claimed:
            raise JobBusy("An apply job is already running; wait for it to finish")
        self.executions[str(job.id)] = record
        return job

    def execution(self, job: T) -> Record:
        record = self.executions.get(str(job.id))
        if record is None:
            raise JobLeaseLost("No local execution handle")
        return record

    async def save(self, job: T, *, in_flight: int | None = None):
        record = self.execution(job)
        record.data = self.adapter.dump_python(job, mode="json")
        record.status = job.status.value
        record.in_flight = in_flight
        await self.runtime.store.save(record)
        if job.status in {JobStatus.DONE, JobStatus.FAILED}:
            self.executions.pop(str(job.id), None)

    async def abort_start(self, job: T):
        record = self.execution(job)
        await fail_record(
            self.runtime.store, record, "Apply was not started; run a new preview"
        )
        self.executions.pop(str(job.id), None)

    async def get(self, job_id: UUID, *, owner: UUID) -> T | None:
        record = await self.runtime.store.get(str(job_id))
        if record is None or record.owner != str(owner) or record.kind != self.kind:
            return None
        try:
            job = self.adapter.validate_python(record.data)
        except (ValueError, TypeError):
            raise JobStoreUnavailable("Invalid apply job state") from None
        job.status = JobStatus(record.status)
        if record.finished is not None:
            job.finished_at = datetime.fromtimestamp(record.finished, UTC)
        if record.error:
            # A synthetic outcome preserves the API schema and every confirmed result.
            reason = record.error
            if record.in_flight is not None:
                reason += f"; transaction {record.in_flight} may have been updated"
            if isinstance(job, BlikApplyJob):
                job.results.append(
                    BlikOutcome(
                        transaction_id=-1,
                        selected_match_id=None,
                        status="failed",
                        reason=reason,
                    )
                )
            else:
                job.results.append(
                    AllegroOutcome(transaction_id=-1, status="failed", reason=reason)
                )
        return job
