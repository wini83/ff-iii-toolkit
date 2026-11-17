from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps_services import get_transaction_snapshot_service
from api.mappers.tx import map_tx_to_api
from api.models.metric_transactions import MetricTransactionsResponse
from services.guards import require_active_user
from services.snapshot.selection import unprocessed_allegro, unprocessed_blik
from services.snapshot.service import TransactionSnapshotService
from settings import settings

router = APIRouter(tags=["statistics"], dependencies=[Depends(require_active_user)])


@router.get(
    "/api/{provider}/statistics/transactions", response_model=MetricTransactionsResponse
)
async def metric_transactions(
    provider: Literal["allegro", "blik"],
    snapshot_timestamp: datetime,
    snapshot_service: Annotated[
        TransactionSnapshotService, Depends(get_transaction_snapshot_service)
    ],
    month: Annotated[str | None, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Literal["date_desc", "date_asc", "amount_desc", "amount_asc"] = "date_desc",
) -> MetricTransactionsResponse:
    # Read once; an intervening publication cannot mix pages with old aggregates.
    # Do not trigger a Firefly fetch just to inspect an existing chart.
    snapshot = await snapshot_service.get_cached_snapshot()
    if snapshot is None or snapshot.fetched_at != snapshot_timestamp:
        raise HTTPException(
            409, "Statistics snapshot changed; reload statistics and transactions"
        )
    if provider == "allegro":
        transactions = unprocessed_allegro(
            snapshot.transactions,
            getattr(settings, "ALLEGRO_DESCRIPTION_FILTER", "allegro"),
        )
    else:
        transactions = unprocessed_blik(
            snapshot.transactions, settings.BLIK_DESCRIPTION_FILTER
        )
    if month is not None:
        transactions = [tx for tx in transactions if tx.date.strftime("%Y-%m") == month]
    if sort.startswith("date"):
        transactions = sorted(
            transactions, key=lambda tx: (tx.date, tx.id), reverse=sort.endswith("desc")
        )
    else:
        transactions = sorted(
            transactions,
            key=lambda tx: (tx.amount, tx.id),
            reverse=sort.endswith("desc"),
        )
    return MetricTransactionsResponse(
        items=[map_tx_to_api(tx) for tx in transactions[offset : offset + limit]],
        total=len(transactions),
        limit=limit,
        offset=offset,
        snapshot_timestamp=snapshot.fetched_at,
        firefly_url=settings.FIREFLY_URL,
    )
