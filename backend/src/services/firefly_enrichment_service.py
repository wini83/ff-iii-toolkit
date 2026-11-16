from collections.abc import Sequence
from datetime import timedelta

from ff_iii_luciferin.api import FireflyAPIError, FireflyClient

from services.domain.bank_record import BankRecord
from services.domain.base import BaseMatchItem
from services.domain.evidence import Evidence
from services.domain.match_result import MatchResult
from services.domain.transaction import Transaction, TxTag
from services.firefly_base_service import (
    FireflyBaseService,
    FireflyServiceError,
    filter_by_description,
)
from services.mappers.firefly import tx_from_ff_tx
from services.matcher import match_transactions
from settings import settings


class FireflyEnrichmentService(FireflyBaseService):
    def __init__(self, firefly_client: FireflyClient):
        super().__init__(firefly_client)

    async def match_with_unmatched(
        self, candidates: Sequence[BaseMatchItem], filter_text: str, tag_done: TxTag
    ) -> tuple[list[MatchResult], list[BaseMatchItem]]:
        min_date = min(r.date for r in candidates)
        max_date = max(r.date for r in candidates) + timedelta(
            days=settings.MATCH_WITH_UNMATCHED_FUTURE_DAYS
        )
        domain_txs = await self.fetch_transactions(
            start_date=min_date, end_date=max_date, exclude_categorized=False
        )
        filtered = filter_by_description(domain_txs, filter_text, exact_match=False)

        return match_transactions(txs=filtered, items=candidates, tag_done=tag_done)

    async def match(
        self, candidates: Sequence[BaseMatchItem], filter_text: str, tag_done: TxTag
    ):
        matches, _ = await self.match_with_unmatched(
            candidates=candidates,
            filter_text=filter_text,
            tag_done=tag_done,
        )
        return matches

    async def apply_match(self, tx: Transaction, evidence: Evidence) -> None:
        # Cached matches identify the user's selection, not the current mutable
        # transaction. Re-read before applying to preserve newer notes and tags.
        current = await self._get_current_transaction(tx.id)
        if (current.date, current.amount, current.type, current.currency.code) != (
            tx.date,
            tx.amount,
            tx.type,
            tx.currency.code,
        ):
            raise FireflyServiceError("Transaction changed; run matching preview again")
        tag_done = (
            TxTag.blik_done if isinstance(evidence, BankRecord) else TxTag.allegro_done
        )
        if current.has_tag(tag_done) and not tx.has_tag(tag_done):
            raise FireflyServiceError(
                "Transaction already processed; run matching preview again"
            )
        payload = evidence.build_tx_update(current)
        await self.update_transaction(current, payload=payload)

    async def _get_current_transaction(self, tx_id: int) -> Transaction:
        try:
            return tx_from_ff_tx(await self.firefly_client.get_transaction(tx_id))
        except FireflyAPIError:
            raise FireflyServiceError(
                "Cannot verify current transaction; run matching preview again"
            ) from None
