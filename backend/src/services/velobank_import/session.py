"""Bounded, user-owned previews; source PDFs are never stored here."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from services.velobank_import.parser import VeloBankStatement

MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_PREVIEWS_PER_USER = 5
MAX_PREVIEWS_GLOBAL = 100
PREVIEW_TTL_SECONDS = 1800


class PreviewNotFound(LookupError):
    """Missing, expired or inaccessible preview."""


class PreviewStoreUnavailable(RuntimeError):
    """Preview storage failed; never silently switch back to process memory."""


class PreviewTooLarge(ValueError):
    """Serialized preview exceeds the storage budget."""


@dataclass(frozen=True)
class ImportPreview:
    id: UUID
    owner: UUID
    statement: VeloBankStatement
    expires_at: datetime


class PreviewStore(Protocol):
    async def create(
        self, owner: UUID, statement: VeloBankStatement
    ) -> ImportPreview: ...
    async def get(self, key: UUID, owner: UUID) -> ImportPreview: ...
    async def delete(self, key: UUID, owner: UUID) -> None: ...


class PreviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: Literal[1]
    id: UUID
    owner: UUID
    statement: VeloBankStatement
    expires_at: AwareDatetime

    @field_validator("statement")
    @classmethod
    def valid_statement(cls, statement: VeloBankStatement) -> VeloBankStatement:
        if (
            len(statement.account) != 26
            or not statement.account.isascii()
            or not statement.account.isdecimal()
        ):
            raise ValueError("Invalid statement account")
        if not 1 <= len(statement.transactions) <= 5000:
            raise ValueError("Invalid operation count")
        for tx in statement.transactions:
            if (
                not tx.description.strip()
                or not tx.amount.is_finite()
                or len(tx.currency) != 3
                or not tx.currency.isascii()
                or not tx.currency.isalpha()
                or not tx.currency.isupper()
            ):
                raise ValueError("Invalid transaction")
        return statement


def serialize_preview(entry: ImportPreview) -> str:
    payload = PreviewPayload(
        schema_version=1,
        id=entry.id,
        owner=entry.owner,
        statement=entry.statement,
        expires_at=entry.expires_at,
    ).model_dump_json()
    if len(payload.encode("utf-8")) > MAX_PREVIEW_BYTES:
        raise PreviewTooLarge()
    return payload


def deserialize_preview(payload: str | bytes) -> ImportPreview:
    if (
        len(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
        > MAX_PREVIEW_BYTES
    ):
        raise ValueError("Oversized preview")
    data = PreviewPayload.model_validate_json(payload)
    return ImportPreview(data.id, data.owner, data.statement, data.expires_at)


class VeloBankPreviewStore:
    """In-memory development/test store with the same limits as Redis."""

    def __init__(self, *, ttl_seconds: int = PREVIEW_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[UUID, ImportPreview] = {}
        self._lock = RLock()

    def purge_expired(self) -> None:
        with self._lock:
            now = datetime.now(UTC)
            for key, entry in list(self._entries.items()):
                if entry.expires_at <= now:
                    del self._entries[key]

    async def create(self, owner: UUID, statement: VeloBankStatement) -> ImportPreview:
        entry = ImportPreview(
            uuid4(),
            owner,
            statement,
            datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
        )
        # Validate before evicting existing previews. Round-trip also isolates
        # mutable transaction lists from the caller, as the Redis adapter does.
        entry = deserialize_preview(serialize_preview(entry))
        with self._lock:
            self.purge_expired()
            own = [key for key, item in self._entries.items() if item.owner == owner]
            if len(own) >= MAX_PREVIEWS_PER_USER:
                del self._entries[own[0]]
            if len(self._entries) >= MAX_PREVIEWS_GLOBAL:
                del self._entries[next(iter(self._entries))]
            self._entries[entry.id] = entry
        return entry

    async def get(self, key: UUID, owner: UUID) -> ImportPreview:
        with self._lock:
            self.purge_expired()
            entry = self._entries.get(key)
            if entry is None or entry.owner != owner:
                raise PreviewNotFound()
            return deserialize_preview(serialize_preview(entry))

    async def delete(self, key: UUID, owner: UUID) -> None:
        with self._lock:
            await self.get(key, owner)
            del self._entries[key]
