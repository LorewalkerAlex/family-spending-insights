from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from family_spending.source_records import SourceRecord
from family_spending.transactions import Transaction

UNCLASSIFIED_CATEGORY = "待分类"
OTHER_EXPENSE_CATEGORY = "其他支出"
GENERAL_SHOPPING_CATEGORY = "综合购物"
HIGH_VALUE_GENERAL_SHOPPING_THRESHOLD = Decimal("1000")
OTHER_EXPENSE_REVIEW = "other_expense_review"
HIGH_VALUE_GENERAL_SHOPPING_REVIEW = "high_value_general_shopping_review"
CategorySource = Literal[
    "merchant_default",
    "transaction_override",
    "manual_override",
    "unclassified",
]


@dataclass(frozen=True)
class TransactionEnrichment:
    transaction_id: str
    merchant_name: str | None
    display_name: str
    default_category: str | None
    category: str
    category_source: CategorySource
    is_unclassified: bool
    review_signals: tuple[str, ...]
    note: str | None = None


class EnrichmentResolver(ABC):
    @abstractmethod
    def resolve(
        self,
        transaction: Transaction,
        source_record: SourceRecord[Any],
    ) -> TransactionEnrichment:
        """Resolve current interpretation separately so editing Merchant or Category never mutates core facts."""


def consumption_review_signals(
    enrichment: TransactionEnrichment,
    spending: Decimal,
) -> tuple[str, ...]:
    """Evaluate amount-dependent review rules after refund netting because raw CMB amounts are not net spending."""
    if spending <= Decimal("0"):
        raise ValueError(f"Net consumption spending must be positive, got {spending!r}")
    if enrichment.category_source in ("transaction_override", "manual_override"):
        return ()
    signals = list(enrichment.review_signals)
    if (
        enrichment.default_category == GENERAL_SHOPPING_CATEGORY
        and spending >= HIGH_VALUE_GENERAL_SHOPPING_THRESHOLD
    ):
        signals.append(HIGH_VALUE_GENERAL_SHOPPING_REVIEW)
    return tuple(signals)


def resolve_enrichments(
    transactions: tuple[Transaction, ...],
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]],
    resolver: EnrichmentResolver,
) -> tuple[TransactionEnrichment, ...]:
    """Join source text only while resolving Enrichment so Transaction Core never needs copied provenance fields."""
    resolved: list[TransactionEnrichment] = []
    for transaction in transactions:
        try:
            source_record = source_records_by_transaction_id[transaction.id]
        except KeyError as exc:
            raise ValueError(
                f"Transaction {transaction.id!r} has no authoritative Source Record for enrichment"
            ) from exc
        resolved.append(resolver.resolve(transaction, source_record))
    return tuple(resolved)
