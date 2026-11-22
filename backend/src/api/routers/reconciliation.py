"""Stateless Alior CSV comparison; marking requires a fresh matching revision."""

import json
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from ff_iii_luciferin.api import FireflyAPIError, FireflyClient

from api.deps_services import get_firefly_client
from services.guards import require_active_user
from services.reconciliation.models import (
    ReconciliationApplyResult,
    ReconciliationPreview,
)
from services.reconciliation.parser import MAX_UPLOAD_BYTES
from services.reconciliation.service import ComparisonChanged, ReconciliationService

router = APIRouter(
    prefix="/api/reconciliation",
    tags=["reconciliation"],
    dependencies=[Depends(require_active_user)],
)
Client = Annotated[FireflyClient, Depends(get_firefly_client)]


async def evidence(file: UploadFile, start: date, end: date) -> bytes:
    if start > end or (end - start).days > 366:
        raise HTTPException(422, "Choose an ordered period of at most 366 days")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Statement exceeds 2 MiB")
    return content


@router.post("/preview", response_model=ReconciliationPreview)
async def preview(
    client: Client,
    file: UploadFile = File(...),
    account_id: int = Form(..., gt=0),
    date_from: date = Form(...),
    date_to: date = Form(...),
    tolerance_days: int = Form(0, ge=0, le=2),
) -> ReconciliationPreview:
    content = await evidence(file, date_from, date_to)
    try:
        result, _ = await ReconciliationService(client).compare(
            content, account_id, date_from, date_to, tolerance_days
        )
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FireflyAPIError as exc:
        raise HTTPException(502, "Could not read Firefly transactions") from exc


@router.post("/apply", response_model=ReconciliationApplyResult)
async def apply(
    client: Client,
    file: UploadFile = File(...),
    account_id: int = Form(..., gt=0),
    date_from: date = Form(...),
    date_to: date = Form(...),
    tolerance_days: int = Form(0, ge=0, le=2),
    revision: str = Form(..., min_length=64, max_length=64),
    selected_ids: str = Form(..., max_length=2000),
) -> ReconciliationApplyResult:
    content = await evidence(file, date_from, date_to)
    try:
        ids = json.loads(selected_ids)
        if not isinstance(ids, list) or any(
            type(item) is not int or item <= 0 for item in ids
        ):
            raise ValueError("Invalid transaction selection")
        return await ReconciliationService(client).apply(
            content, account_id, date_from, date_to, tolerance_days, revision, ids
        )
    except ComparisonChanged as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except FireflyAPIError as exc:
        raise HTTPException(
            502, "Could not revalidate Firefly transactions; compare again"
        ) from exc
