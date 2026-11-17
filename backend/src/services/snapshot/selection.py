"""Selection shared by metric aggregates and transaction details."""

from services.domain.transaction import Transaction, TxTag
from services.firefly_base_service import filter_by_description, filter_out_categorized


def allegro_candidates(
    transactions: list[Transaction], description: str
) -> list[Transaction]:
    return filter_by_description(transactions, description, exact_match=False)


def unprocessed_allegro(
    transactions: list[Transaction], description: str
) -> list[Transaction]:
    return [
        tx
        for tx in allegro_candidates(transactions, description)
        if TxTag.allegro_done not in tx.tags
    ]


def unprocessed_blik(
    transactions: list[Transaction], description: str
) -> list[Transaction]:
    candidates = filter_by_description(
        filter_out_categorized(transactions), description, exact_match=True
    )
    return [tx for tx in candidates if TxTag.blik_done not in tx.tags]
