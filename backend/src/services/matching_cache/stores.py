"""Feature-specific wire contracts; cache JSON never contains secret tokens."""

import logging
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from services.domain.allegro import (
    AllegroOrderPayment,
    AllegroPageMatchCacheEntry,
    AllegroPageRequest,
)
from services.domain.bank_record import BankRecord
from services.domain.match_result import MatchProcessingStatus, MatchResult
from services.domain.transaction import Transaction
from services.matching_cache.backend import (
    MAX_PAYLOAD_BYTES,
    MatchBackend,
    MatchCacheUnavailable,
    MemoryMatchBackend,
    Ticket,
)

logger = logging.getLogger(__name__)


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class BlikMatch(Wire):
    tx: Transaction
    matches: list[BankRecord]
    status: MatchProcessingStatus


class AllegroMatch(Wire):
    tx: Transaction
    matches: list[AllegroOrderPayment]
    status: MatchProcessingStatus


class BlikPayload(Wire):
    schema_version: Literal[1]
    feature: Literal["blik"] = "blik"
    owner: UUID
    file_id: str
    revision: str
    matches: list[BlikMatch]


class AllegroPayload(Wire):
    schema_version: Literal[1]
    feature: Literal["allegro"] = "allegro"
    owner: UUID
    secret_id: UUID
    revision: str
    page: AllegroPageRequest
    login: str
    payments: list[AllegroOrderPayment]
    matches: list[AllegroMatch]
    fetched_at: AwareDatetime


def encode(model, **kwargs) -> str:
    try:
        return model(**kwargs).model_dump_json()
    except (ValueError, TypeError, AttributeError):
        raise MatchCacheUnavailable("Invalid matching cache payload") from None


def encode_match(model, match: MatchResult):
    try:
        return model(tx=match.tx, matches=match.matches, status=match.status)
    except (ValueError, TypeError, AttributeError):
        raise MatchCacheUnavailable("Invalid matching cache payload") from None


def decode(model, payload: str):
    try:
        if len(payload.encode()) > MAX_PAYLOAD_BYTES:
            return None
        return model.model_validate_json(payload)
    except (ValueError, TypeError):
        logger.warning("Ignoring invalid matching cache payload")
        return None


def matches_from_wire(items) -> list[MatchResult]:
    return [MatchResult(item.tx, item.matches, item.status) for item in items]


class BlikMatchStore:
    def __init__(self, backend: MatchBackend | None = None):
        self.backend = backend if backend is not None else MemoryMatchBackend()

    @staticmethod
    def scope(owner: UUID, file_id: str, revision: str) -> str:
        return f"blik:{owner}:{file_id}:{revision}"

    async def begin(self, *, owner: UUID, file_id: str, revision: str) -> Ticket:
        return await self.backend.begin(self.scope(owner, file_id, revision))

    async def put(
        self,
        *,
        owner: UUID,
        file_id: str,
        revision: str,
        matches: list[MatchResult],
        ticket: Ticket | None = None,
    ) -> bool:
        scope = self.scope(owner, file_id, revision)
        ticket = ticket if ticket is not None else await self.backend.begin(scope)
        data = encode(
            BlikPayload,
            schema_version=1,
            owner=owner,
            file_id=file_id,
            revision=revision,
            matches=[encode_match(BlikMatch, m) for m in matches],
        )
        return await self.backend.put(scope, "matches", ticket, data)

    async def get(
        self, *, owner: UUID, file_id: str, revision: str, consume: bool = False
    ) -> list[MatchResult] | None:
        scope = self.scope(owner, file_id, revision)
        if consume:
            values = await self.backend.take(scope)
            raw = values[0] if len(values) == 1 else None
        else:
            raw = await self.backend.read(scope, "matches")
        data = decode(BlikPayload, raw) if raw is not None else None
        if data is None or (data.owner, data.file_id, data.revision) != (
            owner,
            file_id,
            revision,
        ):
            return None
        return matches_from_wire(data.matches)


class AllegroMatchStore:
    def __init__(self, backend: MatchBackend | None = None):
        self.backend = backend if backend is not None else MemoryMatchBackend()

    @staticmethod
    def scope(owner: UUID, secret_id: UUID, revision: str) -> str:
        return f"allegro:{owner}:{secret_id}:{revision}"

    @staticmethod
    def slot(page: AllegroPageRequest) -> str:
        return f"page:{page.limit}:{page.offset}"

    async def begin(self, *, owner: UUID, secret_id: UUID, revision: str) -> Ticket:
        return await self.backend.begin(self.scope(owner, secret_id, revision))

    async def put(
        self,
        *,
        owner: UUID,
        secret_id: UUID,
        revision: str,
        entry: AllegroPageMatchCacheEntry,
        ticket: Ticket | None = None,
    ) -> bool:
        scope = self.scope(owner, secret_id, revision)
        ticket = ticket if ticket is not None else await self.backend.begin(scope)
        data = encode(
            AllegroPayload,
            schema_version=1,
            owner=owner,
            secret_id=secret_id,
            revision=revision,
            page=entry.page,
            login=entry.login,
            payments=entry.payments,
            fetched_at=entry.fetched_at,
            matches=[encode_match(AllegroMatch, m) for m in entry.matches],
        )
        return await self.backend.put(scope, self.slot(entry.page), ticket, data)

    async def get(
        self, *, owner: UUID, secret_id: UUID, revision: str, page: AllegroPageRequest
    ) -> list[MatchResult] | None:
        raw = await self.backend.read(
            self.scope(owner, secret_id, revision), self.slot(page)
        )
        data = decode(AllegroPayload, raw) if raw is not None else None
        if data is None or (data.owner, data.secret_id, data.revision, data.page) != (
            owner,
            secret_id,
            revision,
            page,
        ):
            return None
        return matches_from_wire(data.matches)

    async def get_all(
        self, *, owner: UUID, secret_id: UUID, revision: str, consume: bool = False
    ) -> list[MatchResult]:
        scope = self.scope(owner, secret_id, revision)
        raw_values = (
            await self.backend.take(scope)
            if consume
            else await self.backend.values(scope)
        )
        values = [decode(AllegroPayload, raw) for raw in raw_values]
        # Fail closed for an invalid page: applying only a subset is misleading.
        if any(
            data is None
            or (data.owner, data.secret_id, data.revision)
            != (owner, secret_id, revision)
            for data in values
        ):
            return []
        results = []
        for data in sorted(
            values, key=lambda d: (d.fetched_at, d.page.offset, d.page.limit)
        ):
            results.extend(matches_from_wire(data.matches))
        return results

    async def invalidate(
        self,
        *,
        owner: UUID,
        secret_id: UUID,
        revision: str,
        page: AllegroPageRequest | None = None,
    ) -> bool:
        return await self.backend.invalidate(
            self.scope(owner, secret_id, revision), self.slot(page) if page else None
        )

    async def invalidate_all(self) -> None:
        await self.backend.invalidate_feature("allegro")
