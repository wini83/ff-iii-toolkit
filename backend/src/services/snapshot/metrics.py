from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal

from services.domain.metrics import (
    AllegroMetrics,
    BlikStatisticsMetrics,
    ProviderAmountMetrics,
    TXStatisticsMetrics,
)
from services.domain.transaction import Transaction, TxTag
from services.firefly_base_service import filter_by_description, filter_out_categorized
from services.snapshot.models import TransactionSnapshot
from services.snapshot.selection import (
    allegro_candidates,
    processed_blik,
    unprocessed_allegro,
    unprocessed_blik,
)
from services.snapshot.service import TransactionSnapshotService
from services.tx_stats.helpers import (
    group_tx_amounts_by_month,
    group_tx_by_month,
    sum_tx_amounts,
)


def provider_amount_metrics(
    processed: list[Transaction], not_processed: list[Transaction]
) -> ProviderAmountMetrics:
    """Gross bank amounts, once per transaction, separately for each currency.

    Signs (including refunds) follow categorization metrics: absolute values.
    Coverage is undefined for zero volume. No Allegro order amounts are used.
    """
    processed_amount = sum_tx_amounts(processed)
    not_processed_amount = sum_tx_amounts(not_processed)
    currencies = sorted(processed_amount.keys() | not_processed_amount.keys())
    processed_amount = {c: processed_amount.get(c, Decimal(0)) for c in currencies}
    not_processed_amount = {
        c: not_processed_amount.get(c, Decimal(0)) for c in currencies
    }
    eligible = {c: processed_amount[c] + not_processed_amount[c] for c in currencies}
    return ProviderAmountMetrics(
        eligible_amount=eligible,
        processed_amount=processed_amount,
        not_processed_amount=not_processed_amount,
        processing_coverage={
            c: processed_amount[c] / eligible[c] * 100 if eligible[c] else None
            for c in currencies
        },
        not_processed_amount_by_month=group_tx_amounts_by_month(not_processed),
    )


class SnapshotMetricsService[T](ABC):
    def __init__(self, snapshot_service: TransactionSnapshotService) -> None:
        self.snapshot_service = snapshot_service

    async def fetch_metrics(self) -> T:
        snapshot = await self.snapshot_service.get_snapshot()
        return await self._build_metrics(snapshot)

    async def refresh_metrics(self) -> T:
        snapshot = await self.snapshot_service.refresh_snapshot()
        return await self._build_metrics(snapshot)

    async def get_cached_snapshot_timestamp(self) -> datetime | None:
        return await self.snapshot_service.get_cached_snapshot_timestamp()

    @abstractmethod
    async def _build_metrics(self, snapshot: TransactionSnapshot) -> T:
        raise NotImplementedError


class SnapshotAllegroMetricsService(SnapshotMetricsService[AllegroMetrics]):
    def __init__(
        self,
        snapshot_service: TransactionSnapshotService,
        filter_desc_allegro: str,
    ) -> None:
        super().__init__(snapshot_service)
        self.filter_desc_allegro = filter_desc_allegro

    async def _build_metrics(self, snapshot: TransactionSnapshot) -> AllegroMetrics:
        filtered_by_desc_partial = allegro_candidates(
            snapshot.transactions, self.filter_desc_allegro
        )
        not_processed = unprocessed_allegro(
            snapshot.transactions, self.filter_desc_allegro
        )
        not_processed_by_month = await group_tx_by_month(not_processed)

        return AllegroMetrics(
            total_transactions=snapshot.metrics.total_transactions,
            fetching_duration_ms=snapshot.metrics.fetching_duration_ms,
            amount_metrics=provider_amount_metrics(
                [
                    tx
                    for tx in filtered_by_desc_partial
                    if TxTag.allegro_done in tx.tags
                ],
                not_processed,
            ),
            allegro_transactions=len(filtered_by_desc_partial),
            not_processed_allegro_transactions=len(not_processed),
            not_processed_by_month=not_processed_by_month,
            time_stamp=snapshot.fetched_at,
        )


class SnapshotBlikMetricsService(SnapshotMetricsService[BlikStatisticsMetrics]):
    def __init__(
        self,
        snapshot_service: TransactionSnapshotService,
        filter_desc_blik: str,
    ) -> None:
        super().__init__(snapshot_service)
        self.filter_desc_blik = filter_desc_blik

    async def _build_metrics(
        self, snapshot: TransactionSnapshot
    ) -> BlikStatisticsMetrics:
        uncategorized = filter_out_categorized(snapshot.transactions)
        not_processed = unprocessed_blik(snapshot.transactions, self.filter_desc_blik)
        not_processed_by_month = await group_tx_by_month(not_processed)

        return BlikStatisticsMetrics(
            total_transactions=snapshot.metrics.total_transactions,
            single_part_transactions=len(snapshot.transactions),
            amount_metrics=provider_amount_metrics(
                processed_blik(snapshot.transactions), not_processed
            ),
            uncategorized_transactions=len(uncategorized),
            filtered_by_description_exact=len(not_processed),
            not_processed_transactions=len(not_processed),
            not_processed_by_month=not_processed_by_month,
            time_stamp=snapshot.fetched_at,
            fetching_duration_ms=snapshot.metrics.fetching_duration_ms,
        )


class SnapshotTxMetricsService(SnapshotMetricsService[TXStatisticsMetrics]):
    def __init__(
        self,
        snapshot_service: TransactionSnapshotService,
        filter_desc_blik: str,
        filter_desc_allegro: str,
    ) -> None:
        super().__init__(snapshot_service)
        self.filter_desc_blik = filter_desc_blik
        self.filter_desc_allegro = filter_desc_allegro

    async def _build_metrics(
        self, snapshot: TransactionSnapshot
    ) -> TXStatisticsMetrics:
        txs_uncategorized = filter_out_categorized(snapshot.transactions)
        txs_blik_ok = filter_by_description(
            txs_uncategorized,
            self.filter_desc_blik,
            exact_match=True,
            exclude=True,
        )
        txs_action_not_req = [
            tx for tx in txs_blik_ok if TxTag.action_req not in tx.tags
        ]
        txs_allegro_ok = [
            tx
            for tx in txs_action_not_req
            if not (
                self.filter_desc_allegro in tx.description.lower()
                and TxTag.allegro_done not in tx.tags
            )
        ]
        categorizable_by_month = await group_tx_by_month(txs_allegro_ok)
        txs_blik_not_ok = [tx for tx in txs_uncategorized if tx not in txs_blik_ok]
        txs_action_req = [tx for tx in txs_blik_ok if tx not in txs_action_not_req]
        txs_allegro_not_ok = [
            tx for tx in txs_action_not_req if tx not in txs_allegro_ok
        ]

        return TXStatisticsMetrics(
            total_transactions=snapshot.metrics.total_transactions,
            single_part_transactions=len(snapshot.transactions),
            uncategorized_transactions=len(txs_uncategorized),
            blik_not_ok=len(txs_uncategorized) - len(txs_blik_ok),
            action_req=len(txs_blik_ok) - len(txs_action_not_req),
            allegro_not_ok=len(txs_action_not_req) - len(txs_allegro_ok),
            categorizable=len(txs_allegro_ok),
            categorizable_by_month=categorizable_by_month,
            single_part_amount=sum_tx_amounts(snapshot.transactions),
            uncategorized_amount=sum_tx_amounts(txs_uncategorized),
            blik_not_ok_amount=sum_tx_amounts(txs_blik_not_ok),
            action_req_amount=sum_tx_amounts(txs_action_req),
            allegro_not_ok_amount=sum_tx_amounts(txs_allegro_not_ok),
            categorizable_amount=sum_tx_amounts(txs_allegro_ok),
            categorizable_amount_by_month=group_tx_amounts_by_month(txs_allegro_ok),
            time_stamp=snapshot.fetched_at,
            fetching_duration_ms=snapshot.metrics.fetching_duration_ms,
        )
