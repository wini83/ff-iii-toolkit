"""Bounded, user-owned previews; source PDFs are never stored here."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import UUID, uuid4

from services.velobank_import.parser import VeloBankStatement


class PreviewNotFound(LookupError):
    """Missing, expired or inaccessible preview."""


@dataclass(frozen=True)
class ImportPreview:
    id: UUID
    owner: UUID
    statement: VeloBankStatement
    expires_at: datetime


class VeloBankPreviewStore:
    """Keep at most five previews per user and one hundred per process."""

    def __init__(self, *, ttl_seconds: int = 1800):
        self.ttl_seconds = ttl_seconds
        self._entries: dict[UUID, ImportPreview] = {}
        self._lock = RLock()

    def purge_expired(self) -> None:
        with self._lock:
            now = datetime.now(UTC)
            for key, entry in list(self._entries.items()):
                if entry.expires_at <= now:
                    del self._entries[key]

    def create(self, owner: UUID, statement: VeloBankStatement) -> ImportPreview:
        with self._lock:
            self.purge_expired()
            own = [key for key, entry in self._entries.items() if entry.owner == owner]
            if len(own) >= 5:
                del self._entries[own[0]]
            if len(self._entries) >= 100:
                del self._entries[next(iter(self._entries))]
            entry = ImportPreview(
                uuid4(),
                owner,
                statement,
                datetime.now(UTC) + timedelta(seconds=self.ttl_seconds),
            )
            self._entries[entry.id] = entry
            return entry

    def get(self, key: UUID, owner: UUID) -> ImportPreview:
        with self._lock:
            self.purge_expired()
            entry = self._entries.get(key)
            if entry is None or entry.owner != owner:
                raise PreviewNotFound()
            return entry

    def delete(self, key: UUID, owner: UUID) -> None:
        with self._lock:
            self.get(key, owner)
            del self._entries[key]
