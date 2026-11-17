from datetime import datetime

from pydantic import BaseModel

from api.models.tx import SimplifiedTx


class MetricTransactionsResponse(BaseModel):
    items: list[SimplifiedTx]
    total: int
    limit: int
    offset: int
    snapshot_timestamp: datetime
    firefly_url: str | None
