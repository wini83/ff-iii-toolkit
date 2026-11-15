"""Versioned JSON format for the shared snapshot. Never use pickle for cache data."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from services.domain.metrics import FetchMetrics
from services.domain.transaction import Transaction
from services.snapshot.models import TransactionSnapshot


class InvalidSnapshot(ValueError):
    """Snapshot does not satisfy the versioned wire contract."""


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, revalidate_instances="always", allow_inf_nan=False
    )

    schema_version: Literal[1]
    fetched_at: AwareDatetime
    transactions: list[Transaction]
    metrics: FetchMetrics


def serialize_snapshot(snapshot: TransactionSnapshot) -> str:
    try:
        payload = SnapshotPayload.model_validate(
            {
                "schema_version": snapshot.schema_version,
                "fetched_at": snapshot.fetched_at,
                "transactions": snapshot.transactions,
                "metrics": snapshot.metrics,
            }
        )
        return payload.model_dump_json()
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise InvalidSnapshot("Invalid transaction snapshot") from None


def deserialize_snapshot(payload: str | bytes) -> TransactionSnapshot:
    try:
        data = SnapshotPayload.model_validate_json(payload)
    except (ValidationError, ValueError, TypeError):
        raise InvalidSnapshot("Invalid transaction snapshot payload") from None
    return TransactionSnapshot(
        transactions=data.transactions,
        metrics=data.metrics,
        fetched_at=data.fetched_at,
        schema_version=data.schema_version,
    )
