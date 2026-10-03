"""PDF to preview to CSV; this router never writes to Firefly."""

from io import BytesIO
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from api.deps_db import get_db
from api.deps_runtime import get_velobank_preview_store
from api.models.velobank_import import (
    VeloBankAccountOption,
    VeloBankMappings,
    VeloBankPreviewResponse,
)
from services.db.models import VeloBankProfileORM
from services.guards import require_active_user
from services.velobank_import.accounts import fetch_asset_accounts
from services.velobank_import.parser import VeloBankParseError, parse_pdf
from services.velobank_import.service import export_payload, preview_data
from services.velobank_import.session import PreviewNotFound, VeloBankPreviewStore
from settings import settings

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def no_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    prefix="/api/tools/velobank",
    tags=["velobank-import"],
    dependencies=[Depends(require_active_user), Depends(no_cache)],
)


def saved_mappings(db: Session, user: UUID) -> dict[str, str]:
    profile = db.get(VeloBankProfileORM, user)
    return dict(profile.accounts_json) if profile else {}


def owned_preview(store: VeloBankPreviewStore, file_id: UUID, user: UUID):
    try:
        return store.get(file_id, user)
    except PreviewNotFound as exc:
        raise HTTPException(
            404, "Preview expired or unavailable. Upload the PDF again."
        ) from exc


@router.get("/accounts", response_model=list[VeloBankAccountOption])
async def list_accounts():
    if not settings.FIREFLY_URL or not settings.FIREFLY_TOKEN:
        raise HTTPException(
            503, "Firefly is not configured. Enter account names manually."
        )
    try:
        return await fetch_asset_accounts(settings.FIREFLY_URL, settings.FIREFLY_TOKEN)
    except (httpx.HTTPError, ValidationError, ValueError) as exc:
        raise HTTPException(
            502, "Cannot load Firefly accounts. Enter account names manually or retry."
        ) from exc


@router.get("/mappings", response_model=VeloBankMappings)
def get_mappings(
    user: UUID = Depends(require_active_user), db: Session = Depends(get_db)
):
    return VeloBankMappings(accounts=saved_mappings(db, user))


@router.put("/mappings", response_model=VeloBankMappings)
def save_mappings(
    payload: VeloBankMappings,
    user: UUID = Depends(require_active_user),
    db: Session = Depends(get_db),
):
    profile = db.get(VeloBankProfileORM, user)
    if profile is None:
        profile = VeloBankProfileORM(user_id=user)
        db.add(profile)
    profile.accounts_json = payload.accounts
    db.commit()
    return payload


@router.post("/upload", response_model=VeloBankPreviewResponse)
async def upload_pdf(
    file: UploadFile = File(...),
    user: UUID = Depends(require_active_user),
    db: Session = Depends(get_db),
    store: VeloBankPreviewStore = Depends(get_velobank_preview_store),
):
    try:
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(400, "Choose a VeloBank PDF account history.")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "PDF exceeds the 10 MiB upload limit.")
        if not content.startswith(b"%PDF-"):
            raise HTTPException(400, "The uploaded file is not a PDF.")
        try:
            statement = await run_in_threadpool(
                parse_pdf, BytesIO(content), max_pages=50
            )
        except VeloBankParseError as exc:
            raise HTTPException(400, str(exc)) from exc
        if len(statement.transactions) > 5000:
            raise HTTPException(
                400,
                "History exceeds the 5000-operation limit. Export a shorter period.",
            )
        entry = store.create(user, statement)
        return preview_data(entry, saved_mappings(db, user))
    finally:
        # Starlette may spool uploads to a temporary file; close removes it.
        await file.close()


@router.get("/files/{file_id}", response_model=VeloBankPreviewResponse)
def get_preview(
    file_id: UUID,
    user: UUID = Depends(require_active_user),
    db: Session = Depends(get_db),
    store: VeloBankPreviewStore = Depends(get_velobank_preview_store),
):
    return preview_data(owned_preview(store, file_id, user), saved_mappings(db, user))


@router.post("/files/{file_id}/preview", response_model=VeloBankPreviewResponse)
def configure_preview(
    file_id: UUID,
    payload: VeloBankMappings,
    user: UUID = Depends(require_active_user),
    store: VeloBankPreviewStore = Depends(get_velobank_preview_store),
):
    return preview_data(owned_preview(store, file_id, user), payload.accounts)


@router.post("/files/{file_id}/export-csv")
def export_csv(
    file_id: UUID,
    payload: VeloBankMappings,
    chunk_size: int | None = Query(default=None, ge=1, le=1000),
    user: UUID = Depends(require_active_user),
    store: VeloBankPreviewStore = Depends(get_velobank_preview_store),
):
    entry = owned_preview(store, file_id, user)
    try:
        content, filename, media_type = export_payload(
            entry, payload.accounts, chunk_size
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("/files/{file_id}", status_code=204)
def discard_preview(
    file_id: UUID,
    user: UUID = Depends(require_active_user),
    store: VeloBankPreviewStore = Depends(get_velobank_preview_store),
):
    owned_preview(store, file_id, user)
    store.delete(file_id, user)
