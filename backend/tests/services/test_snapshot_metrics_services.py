import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.domain.metrics import FetchMetrics
from services.domain.transaction import Category, Currency, Transaction, TxTag, TxType
from services.snapshot.metrics import (
    SnapshotAllegroMetricsService,
    SnapshotBlikMetricsService,
    SnapshotTxMetricsService,
)
from services.snapshot.models import TransactionSnapshot

DEFAULT_CURRENCY = Currency(code="PLN", symbol="zl", decimals=2)


def _tx(
    tx_id: int,
    *,
    tx_date: date,
    description: str,
    tags: set[str] | None = None,
    category: Category | None = None,
    amount: Decimal = Decimal("10.00"),
    currency: Currency = DEFAULT_CURRENCY,
) -> Transaction:
    return Transaction(
        id=tx_id,
        date=tx_date,
        amount=amount,
        type=TxType.WITHDRAWAL,
        description=description,
        tags=tags or set(),
        notes=None,
        category=category,
        currency=currency,
    )


def _snapshot(
    transactions: list[Transaction], *, fetched_at: datetime | None = None
) -> TransactionSnapshot:
    return TransactionSnapshot(
        transactions=transactions,
        metrics=FetchMetrics(
            total_transactions=50,
            fetching_duration_ms=250,
            invalid=0,
            multipart=0,
        ),
        fetched_at=fetched_at or datetime.now(UTC),
    )


def test_snapshot_blik_metrics_aggregates_counts_and_months():
    transactions = [
        _tx(1, tx_date=date(2024, 1, 5), description="blik"),
        _tx(
            2,
            tx_date=date(2024, 1, 15),
            description="blik",
            tags={TxTag.blik_done},
        ),
        _tx(3, tx_date=date(2024, 2, 1), description="blik groceries"),
        _tx(4, tx_date=date(2024, 2, 10), description="other"),
        _tx(
            5,
            tx_date=date(2024, 2, 12),
            description="blik",
            category=Category(id=1, name="Food"),
        ),
    ]
    fetched_at = datetime(2024, 3, 1, 10, 30, tzinfo=UTC)
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(
        return_value=_snapshot(transactions, fetched_at=fetched_at)
    )
    service = SnapshotBlikMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_blik="blik",
    )

    stats = asyncio.run(service.fetch_metrics())

    assert stats.total_transactions == 50
    assert stats.single_part_transactions == 5
    assert stats.uncategorized_transactions == 4
    assert stats.filtered_by_description_exact == 1
    assert stats.not_processed_transactions == 1
    assert stats.not_processed_by_month == {"2024-01": 1}
    assert stats.time_stamp == fetched_at


def test_snapshot_allegro_metrics_aggregates_counts_and_months():
    transactions = [
        _tx(1, tx_date=date(2024, 1, 5), description="allegro order"),
        _tx(
            2,
            tx_date=date(2024, 1, 20),
            description="allegro order",
            tags={TxTag.allegro_done},
            amount=Decimal("44.00"),
        ),
        _tx(3, tx_date=date(2024, 2, 8), description="other"),
    ]
    fetched_at = datetime(2024, 3, 2, 9, 15, tzinfo=UTC)
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(
        return_value=_snapshot(transactions, fetched_at=fetched_at)
    )
    service = SnapshotAllegroMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_allegro="allegro",
    )

    stats = asyncio.run(service.fetch_metrics())

    assert stats.total_transactions == 50
    assert stats.allegro_transactions == 2
    assert stats.not_processed_allegro_transactions == 1
    assert stats.not_processed_by_month == {"2024-01": 1}
    assert stats.time_stamp == fetched_at


def test_snapshot_tx_metrics_aggregates_categorizable_counts():
    transactions = [
        _tx(1, tx_date=date(2024, 1, 1), description="blik", amount=Decimal("11.00")),
        _tx(
            2,
            tx_date=date(2024, 1, 2),
            description="other",
            tags={TxTag.action_req},
            amount=Decimal("22.00"),
        ),
        _tx(
            3,
            tx_date=date(2024, 1, 3),
            description="allegro order",
            amount=Decimal("33.00"),
        ),
        _tx(
            4,
            tx_date=date(2024, 1, 4),
            description="allegro order",
            tags={TxTag.allegro_done},
        ),
        _tx(
            5,
            tx_date=date(2024, 1, 5),
            description="groceries",
            amount=Decimal("-55.00"),
        ),
    ]
    fetched_at = datetime(2024, 3, 3, 8, 0, tzinfo=UTC)
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(
        return_value=_snapshot(transactions, fetched_at=fetched_at)
    )
    service = SnapshotTxMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_blik="blik",
        filter_desc_allegro="allegro",
    )

    stats = asyncio.run(service.fetch_metrics())

    assert stats.total_transactions == 50
    assert stats.single_part_transactions == 5
    assert stats.uncategorized_transactions == 5
    assert stats.blik_not_ok == 1
    assert stats.action_req == 1
    assert stats.allegro_not_ok == 1
    assert stats.categorizable == 2
    assert stats.categorizable_by_month == {"2024-01": 2}
    assert stats.single_part_amount == {"PLN": Decimal("131.00")}
    assert stats.uncategorized_amount == {"PLN": Decimal("131.00")}
    assert stats.blik_not_ok_amount == {"PLN": Decimal("11.00")}
    assert stats.action_req_amount == {"PLN": Decimal("22.00")}
    assert stats.allegro_not_ok_amount == {"PLN": Decimal("33.00")}
    assert stats.categorizable_amount == {"PLN": Decimal("65.00")}
    assert stats.categorizable_amount_by_month == {"2024-01": {"PLN": Decimal("65.00")}}
    assert stats.time_stamp == fetched_at


def test_snapshot_blik_refresh_metrics_uses_forced_snapshot_refresh():
    fetched_at = datetime(2024, 3, 4, 12, 0, tzinfo=UTC)
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock()
    snapshot_service.refresh_snapshot = AsyncMock(
        return_value=_snapshot(
            [_tx(1, tx_date=date(2024, 1, 5), description="blik")],
            fetched_at=fetched_at,
        )
    )
    service = SnapshotBlikMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_blik="blik",
    )

    stats = asyncio.run(service.refresh_metrics())

    snapshot_service.get_snapshot.assert_not_awaited()
    snapshot_service.refresh_snapshot.assert_awaited_once()
    assert stats.time_stamp == fetched_at


def test_snapshot_tx_metrics_keeps_mixed_currency_amounts_separate():
    eur = Currency(code="EUR", symbol="€", decimals=2)
    transactions = [
        _tx(
            1,
            tx_date=date(2024, 1, 1),
            description="groceries",
            amount=Decimal("10.00"),
        ),
        _tx(
            2,
            tx_date=date(2024, 1, 2),
            description="coffee",
            amount=Decimal("-5.00"),
            currency=eur,
        ),
    ]
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(return_value=_snapshot(transactions))
    service = SnapshotTxMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_blik="blik",
        filter_desc_allegro="allegro",
    )

    stats = asyncio.run(service.fetch_metrics())

    expected = {"EUR": Decimal("5.00"), "PLN": Decimal("10.00")}
    assert stats.single_part_transactions == 2
    assert stats.categorizable == 2
    assert stats.single_part_amount == expected
    assert stats.categorizable_amount == expected
    assert stats.categorizable_amount_by_month == {"2024-01": expected}


def test_snapshot_tx_metrics_empty_snapshot_has_empty_amounts():
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(return_value=_snapshot([]))
    service = SnapshotTxMetricsService(
        snapshot_service=snapshot_service,
        filter_desc_blik="blik",
        filter_desc_allegro="allegro",
    )

    stats = asyncio.run(service.fetch_metrics())

    assert stats.single_part_transactions == 0
    assert stats.single_part_amount == {}
    assert stats.uncategorized_amount == {}
    assert stats.categorizable_amount == {}
    assert stats.categorizable_amount_by_month == {}


@pytest.mark.parametrize("provider", ["allegro", "blik"])
def test_provider_amounts_reconcile_by_currency_and_preserve_precision(provider):
    description = "allegro" if provider == "allegro" else "blik"
    tag = TxTag.allegro_done if provider == "allegro" else TxTag.blik_done
    eur = Currency(code="EUR", symbol="€", decimals=2)
    # One bank payment may represent multiple orders; only its bank amount counts.
    paid = _tx(
        1,
        tx_date=date(2024, 1, 1),
        description=description,
        amount=Decimal("-0.10"),
        tags={tag},
    )
    paid.notes = "Matched orders: order-a, order-b"
    if provider == "blik":
        paid.description = "merchant after processing"
        paid.category = Category(id=1, name="Food")
    transactions = [
        paid,
        _tx(
            2, tx_date=date(2024, 1, 2), description=description, amount=Decimal("0.20")
        ),
        _tx(
            3,
            tx_date=date(2024, 2, 1),
            description=description,
            amount=Decimal("-1.01"),
            currency=eur,
        ),
        _tx(
            4,
            tx_date=date(2024, 2, 2),
            description=description,
            amount=Decimal("2.02"),
            currency=eur,
            tags={tag},
        ),
        _tx(
            5,
            tx_date=date(2024, 1, 1),
            description="unrelated",
            amount=Decimal("999.99"),
        ),
    ]
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(return_value=_snapshot(transactions))
    cls = (
        SnapshotAllegroMetricsService
        if provider == "allegro"
        else SnapshotBlikMetricsService
    )
    stats = asyncio.run(cls(snapshot_service, description).fetch_metrics())
    amounts = stats.amount_metrics
    assert amounts.eligible_amount == {"PLN": Decimal("0.30"), "EUR": Decimal("3.03")}
    assert amounts.processed_amount == {"PLN": Decimal("0.10"), "EUR": Decimal("2.02")}
    assert amounts.not_processed_amount == {
        "PLN": Decimal("0.20"),
        "EUR": Decimal("1.01"),
    }
    assert amounts.not_processed_amount_by_month == {
        "2024-01": {"PLN": Decimal("0.20")},
        "2024-02": {"EUR": Decimal("1.01")},
    }
    assert amounts.processing_coverage["PLN"] == Decimal("0.10") / Decimal("0.30") * 100
    assert amounts.processing_coverage["EUR"] == Decimal("2.02") / Decimal("3.03") * 100
    for currency, total in amounts.eligible_amount.items():
        assert (
            total
            == amounts.processed_amount[currency]
            + amounts.not_processed_amount[currency]
        )


@pytest.mark.parametrize("provider", ["allegro", "blik"])
@pytest.mark.parametrize("empty", [False, True])
def test_provider_amounts_zero_and_empty_snapshots(provider, empty):
    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(
        return_value=_snapshot(
            []
            if empty
            else [
                _tx(
                    1, tx_date=date(2024, 1, 1), description=provider, amount=Decimal(0)
                )
            ]
        )
    )
    cls = (
        SnapshotAllegroMetricsService
        if provider == "allegro"
        else SnapshotBlikMetricsService
    )
    stats = asyncio.run(cls(snapshot_service, provider).fetch_metrics())
    assert stats.amount_metrics.eligible_amount == (
        {} if empty else {"PLN": Decimal(0)}
    )
    assert stats.amount_metrics.processing_coverage == ({} if empty else {"PLN": None})


@pytest.mark.parametrize("provider", ["allegro", "blik"])
def test_provider_amounts_serialize_decimal_strings_and_support_legacy_cache(provider):
    from dataclasses import asdict

    from pydantic import TypeAdapter

    from api.mappers.allegro import map_allegro_metrics_state_to_response
    from api.mappers.blik_stats import map_blik_metrics_state_to_response
    from services.domain.job_base import JobStatus
    from services.tx_stats.models import MetricsState

    snapshot_service = MagicMock()
    snapshot_service.get_snapshot = AsyncMock(
        return_value=_snapshot(
            [
                _tx(
                    1,
                    tx_date=date(2024, 1, 1),
                    description=provider,
                    amount=Decimal("0.10"),
                )
            ]
        )
    )
    cls, mapper = (
        (SnapshotAllegroMetricsService, map_allegro_metrics_state_to_response)
        if provider == "allegro"
        else (SnapshotBlikMetricsService, map_blik_metrics_state_to_response)
    )
    metrics = asyncio.run(cls(snapshot_service, provider).fetch_metrics())
    state = MetricsState(
        status=JobStatus.DONE,
        result=metrics,
        error=None,
        progress=None,
        last_updated_at=metrics.time_stamp,
    )
    payload = mapper(state).model_dump(mode="json")["result"]["amount_metrics"]
    assert payload["eligible_amount"] == {"PLN": "0.10"}
    assert isinstance(payload["processing_coverage"]["PLN"], str)
    assert Decimal(payload["processing_coverage"]["PLN"]) == 0
    legacy = asdict(metrics)
    legacy.pop("amount_metrics")
    state.result = TypeAdapter(type(metrics)).validate_python(legacy)
    assert mapper(state).result.amount_metrics is None
