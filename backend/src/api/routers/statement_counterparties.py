from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from ff_iii_luciferin.api import FireflyAPIError, FireflyClient
from pydantic import Field, Json

from api.deps_services import get_firefly_client
from api.mappers.blik import map_job_to_response
from api.models.blik_files import ApplyJobResponse
from services.domain.blik import BlikApplyJob
from services.guards import require_active_user
from services.jobs.apply import ApplyJobManager
from services.reconciliation.parser import MAX_UPLOAD_BYTES
from services.statement_counterparties import (
    CounterpartyPreview,
    CounterpartyPreviewChanged,
    StatementAccountOption,
    StatementCounterpartyService,
    account_options,
)
from settings import settings

router = APIRouter(
    prefix="/api/statement-counterparties",
    tags=["statement-counterparties"],
    dependencies=[Depends(require_active_user)],
)
Client = Annotated[FireflyClient, Depends(get_firefly_client)]
User = Annotated[UUID, Depends(require_active_user)]
StatementFile = Annotated[UploadFile, File()]
AccountId = Annotated[int, Form(gt=0)]


class CounterpartyJobResponse(ApplyJobResponse):
    firefly_url: str | None


def get_counterparty_jobs(request: Request) -> ApplyJobManager[BlikApplyJob]:
    return request.app.state.counterparty_jobs


def get_counterparty_service(
    request: Request,
    client: Client,
    jobs: Annotated[ApplyJobManager[BlikApplyJob], Depends(get_counterparty_jobs)],
) -> StatementCounterpartyService:
    return StatementCounterpartyService(
        client,
        jobs,
        request.app.state.matching_backend,
        request.app.state.snapshot_store,
    )


Service = Annotated[StatementCounterpartyService, Depends(get_counterparty_service)]


async def read_statement(file: UploadFile) -> bytes:
    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Statement exceeds 2 MiB.")
    return content


def job_response(job: BlikApplyJob) -> CounterpartyJobResponse:
    return CounterpartyJobResponse(
        **map_job_to_response(job).model_dump(), firefly_url=settings.FIREFLY_URL
    )


@router.get("/accounts")
async def statement_accounts(client: Client) -> list[StatementAccountOption]:
    try:
        return account_options(await client.fetch_accounts(account_type="asset"))
    except FireflyAPIError as exc:
        raise HTTPException(502, "Could not load Firefly accounts.") from exc


@router.post("/preview")
async def preview_counterparties(
    service: Service,
    file: StatementFile,
    account_id: AccountId,
    single_account_confirmed: Annotated[bool, Form()] = False,
) -> CounterpartyPreview:
    content = await read_statement(file)
    try:
        preview, _, _ = await service.plan(
            content, account_id, single_account_confirmed=single_account_confirmed
        )
        return preview
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FireflyAPIError as exc:
        raise HTTPException(
            502, "Could not read Firefly accounts or transactions."
        ) from exc


@router.post("/apply", status_code=202)
async def apply_counterparties(
    service: Service,
    user: User,
    file: StatementFile,
    account_id: AccountId,
    revision: Annotated[str, Form(pattern=r"^[a-f0-9]{64}$")],
    single_account_confirmed: Annotated[bool, Form()] = False,
    account_choices: Annotated[
        Json[dict[str, Annotated[int, Field(strict=True, gt=0)]]] | None, Form()
    ] = None,
) -> CounterpartyJobResponse:
    content = await read_statement(file)
    try:
        return job_response(
            await service.start(
                user,
                content,
                account_id,
                revision,
                single_account_confirmed=single_account_confirmed,
                account_choices=account_choices,
            )
        )
    except CounterpartyPreviewChanged as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FireflyAPIError as exc:
        raise HTTPException(
            502, "Could not revalidate the preview against Firefly."
        ) from exc


@router.get("/jobs/{job_id}")
async def counterparty_job_status(
    job_id: UUID,
    user: User,
    jobs: Annotated[ApplyJobManager[BlikApplyJob], Depends(get_counterparty_jobs)],
) -> CounterpartyJobResponse:
    job = await jobs.get(job_id, owner=user)
    if job is None:
        raise HTTPException(404, "Job not found or expired.")
    return job_response(job)
