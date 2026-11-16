"""Own local asyncio tasks and renew their shared execution leases."""

import asyncio
import logging
from collections.abc import Coroutine
from contextlib import suppress

from services.jobs.store import JobLeaseLost, JobStore, JobStoreUnavailable, Record

logger = logging.getLogger(__name__)


class JobRuntime:
    def __init__(self, store: JobStore):
        self.store = store
        self.tasks: set[asyncio.Task] = set()
        self.records: dict[asyncio.Task, Record] = {}

    def spawn(self, record: Record, work: Coroutine) -> None:
        started = False

        async def execute():
            nonlocal started
            started = True
            await self._run(record, work)

        def completed(task):
            if not started:
                work.close()
            self._done(task)

        task = asyncio.create_task(execute())
        self.tasks.add(task)
        self.records[task] = record
        task.add_done_callback(completed)

    def _done(self, task):
        self.tasks.discard(task)
        self.records.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background job stopped; verify its shared state")

    async def _run(self, record: Record, work: Coroutine):
        async def renew():
            while True:
                await asyncio.sleep(max(0.05, self.store.lease_seconds / 3))
                await self.store.heartbeat(record.id, record.token)

        body = asyncio.create_task(work)
        heartbeat = asyncio.create_task(renew())
        try:
            done, _ = await asyncio.wait(
                {body, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                # Losing storage/ownership stops further financial requests.
                heartbeat.result()
            await body
        except (JobLeaseLost, JobStoreUnavailable):
            logger.warning("Job execution lost its lease or storage connection")
        finally:
            body.cancel()
            heartbeat.cancel()
            await asyncio.gather(body, heartbeat, return_exceptions=True)
            if body.cancelled() or body.exception() is not None:
                await fail_record(
                    self.store,
                    record,
                    "Execution interrupted; verify results before retrying",
                )

    async def aclose(self):
        tasks = list(self.tasks)
        records = list(self.records.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
        self.records.clear()
        for record in records:
            await fail_record(
                self.store,
                record,
                "Execution interrupted; verify results before retrying",
            )


async def fail_record(store: JobStore, record: Record, reason: str):
    record.status = "failed"
    record.error = reason
    with suppress(JobLeaseLost, JobStoreUnavailable):
        await store.save(record)
