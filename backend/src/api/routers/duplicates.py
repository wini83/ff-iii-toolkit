from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from api.deps_services import get_transaction_snapshot_service
from api.mappers.tx import map_tx_to_api
from api.models.duplicates import (
    DuplicateOptions,
    DuplicatePairResponse,
    DuplicateResults,
)
from api.models.tx import SimplifiedAccountRef
from services.domain.transaction import AccountType
from services.duplicates import find_duplicate_page
from services.firefly_base_service import FireflyServiceError
from services.guards import require_active_user
from services.snapshot.coordination import SnapshotRefreshTimeout
from services.snapshot.service import TransactionSnapshotService
from settings import settings

router = APIRouter(
    prefix="/api/tx/duplicates",
    tags=["transactions"],
    dependencies=[Depends(require_active_user)],
)
SnapshotService = Annotated[
    TransactionSnapshotService, Depends(get_transaction_snapshot_service)
]


@router.get("/options", response_model=DuplicateOptions)
async def duplicate_options(snapshot_service: SnapshotService) -> DuplicateOptions:
    return await _load_options(snapshot_service, refresh=False)


@router.post("/options/refresh", response_model=DuplicateOptions)
async def refresh_duplicate_options(
    snapshot_service: SnapshotService,
) -> DuplicateOptions:
    return await _load_options(snapshot_service, refresh=True)


async def _load_options(
    snapshot_service: TransactionSnapshotService, *, refresh: bool
) -> DuplicateOptions:
    try:
        snapshot = (
            await snapshot_service.refresh_snapshot()
            if refresh
            else await snapshot_service.get_snapshot()
        )
    except FireflyServiceError as exc:
        raise HTTPException(502, "Failed to fetch Firefly transactions") from exc
    except (SnapshotRefreshTimeout, TimeoutError) as exc:
        raise HTTPException(504, "Transaction snapshot refresh timed out") from exc
    accounts = {}
    dates = []
    for tx in snapshot.transactions:
        dates.append(tx.date)
        for account in (tx.source_account, tx.destination_account):
            if account is not None and account.type == AccountType.ASSET:
                accounts[account.id] = SimplifiedAccountRef(
                    id=account.id,
                    name=account.name,
                    type=account.type.value,
                    iban=account.iban,
                )
    return DuplicateOptions(
        accounts=sorted(
            accounts.values(), key=lambda account: (account.name.casefold(), account.id)
        ),
        date_from=min(dates) if dates else None,
        date_to=max(dates) if dates else None,
        snapshot_timestamp=snapshot.fetched_at,
        skipped_multipart=snapshot.metrics.multipart,
    )


@router.get("", response_model=DuplicateResults)
async def duplicate_results(
    snapshot_service: SnapshotService,
    account_id: Annotated[int, Query(gt=0)],
    date_from: date,
    date_to: date,
    snapshot_timestamp: datetime,
    tolerance_days: Annotated[int, Query(ge=0, le=7)] = 2,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> DuplicateResults:
    if date_from > date_to:
        raise HTTPException(422, "Date from must not be after date to")
    snapshot = await snapshot_service.get_cached_snapshot()
    if snapshot is None or snapshot.fetched_at != snapshot_timestamp:
        raise HTTPException(
            409, "Snapshot changed. Reload transactions and search again."
        )
    pairs, total = await run_in_threadpool(
        find_duplicate_page,
        snapshot.transactions,
        account_id=account_id,
        date_from=date_from,
        date_to=date_to,
        tolerance_days=tolerance_days,
        offset=offset,
        limit=limit,
    )
    return DuplicateResults(
        items=[
            DuplicatePairResponse(
                id=f"{pair.first.id}:{pair.second.id}",
                first=map_tx_to_api(pair.first),
                second=map_tx_to_api(pair.second),
                amount=str(abs(pair.first.amount)),
                currency_code=pair.first.currency.code,
                direction=pair.direction,
                date_difference_days=abs((pair.first.date - pair.second.date).days),
            )
            for pair in pairs
        ],
        total=total,
        offset=offset,
        limit=limit,
        snapshot_timestamp=snapshot.fetched_at,
        firefly_url=settings.FIREFLY_URL,
        skipped_multipart=snapshot.metrics.multipart,
    )
