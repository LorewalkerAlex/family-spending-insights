from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from family_spending.source_records import SourceRecord
from family_spending.transactions import Transaction

UNCLASSIFIED_CATEGORY = "待分类"
INCOME_DEFAULT_CATEGORY = "其他收入"
OTHER_EXPENSE_CATEGORY = "其他支出"
GENERAL_SHOPPING_CATEGORY = "综合购物"
HIGH_VALUE_GENERAL_SHOPPING_THRESHOLD = Decimal("1000")
OTHER_EXPENSE_REVIEW = "other_expense_review"
HIGH_VALUE_GENERAL_SHOPPING_REVIEW = "high_value_general_shopping_review"
CategorySource = Literal[
    "merchant_default",
    "transaction_override",
    "manual_override",
    "income_default",
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


@dataclass(frozen=True)
class TransactionEnrichmentState:
    transaction_id: str
    merchant_name: str | None
    default_category: str | None
    category: str
    category_source: CategorySource
    note: str | None = None


def enrichment_state_from_result(
    enrichment: TransactionEnrichment,
) -> TransactionEnrichmentState:
    """Persist only current user-manageable Enrichment, leaving display and review fields derived."""
    return TransactionEnrichmentState(
        transaction_id=enrichment.transaction_id,
        merchant_name=enrichment.merchant_name,
        default_category=enrichment.default_category,
        category=enrichment.category,
        category_source=enrichment.category_source,
        note=enrichment.note,
    )


def materialize_enrichment_state(
    state: TransactionEnrichmentState,
    source_record: SourceRecord[Any],
) -> TransactionEnrichment:
    """Join current persisted Enrichment with source text only when a runtime/query view needs it."""
    review_signals = (
        (OTHER_EXPENSE_REVIEW,)
        if state.category_source == "merchant_default"
        and state.default_category == OTHER_EXPENSE_CATEGORY
        else ()
    )
    return TransactionEnrichment(
        transaction_id=state.transaction_id,
        merchant_name=state.merchant_name,
        display_name=state.merchant_name or source_record.description or source_record.id,
        default_category=state.default_category,
        category=state.category,
        category_source=state.category_source,
        is_unclassified=state.category == UNCLASSIFIED_CATEGORY,
        review_signals=review_signals,
        note=state.note,
    )


def update_merchant_enrichment_state(
    state: TransactionEnrichmentState,
    *,
    merchant_name: str | None,
    default_category: str | None,
) -> TransactionEnrichmentState:
    """Re-evaluate only implicit Category when Merchant changes; explicit overrides remain user-owned."""
    if state.category_source == "income_default":
        return state
    if state.category_source in ("transaction_override", "manual_override"):
        category = state.category
        category_source = state.category_source
    elif default_category is None:
        category = UNCLASSIFIED_CATEGORY
        category_source = "unclassified"
    else:
        category = default_category
        category_source = "merchant_default"
    return TransactionEnrichmentState(
        transaction_id=state.transaction_id,
        merchant_name=merchant_name,
        default_category=default_category,
        category=category,
        category_source=category_source,
        note=state.note,
    )


def update_category_enrichment_state(
    state: TransactionEnrichmentState,
    category: str | None,
) -> TransactionEnrichmentState:
    """Set an explicit current Category, or reset it to the current Merchant default when null."""
    if state.category_source == "income_default":
        return state
    elif category is None:
        if state.default_category is None:
            next_category = UNCLASSIFIED_CATEGORY
            category_source = "unclassified"
        else:
            next_category = state.default_category
            category_source = "merchant_default"
    else:
        next_category = category
        category_source = "manual_override"
    return TransactionEnrichmentState(
        transaction_id=state.transaction_id,
        merchant_name=state.merchant_name,
        default_category=state.default_category,
        category=next_category,
        category_source=category_source,
        note=state.note,
    )


def update_note_enrichment_state(
    state: TransactionEnrichmentState,
    note: str | None,
) -> TransactionEnrichmentState:
    """Change Note without disturbing Merchant or Category interpretation."""
    return TransactionEnrichmentState(
        transaction_id=state.transaction_id,
        merchant_name=state.merchant_name,
        default_category=state.default_category,
        category=state.category,
        category_source=state.category_source,
        note=note,
    )


def validate_enrichment_state_categories(
    state: TransactionEnrichmentState,
    categories: Collection[str],
) -> None:
    """Reject persisted current categories that no longer fit their formal expense or income vocabulary."""
    if state.category_source == "income_default":
        if state.merchant_name is not None:
            raise ValueError(
                f"Income Transaction {state.transaction_id!r} must not have a Merchant in income_default state"
            )
        if state.default_category is not None or state.category != INCOME_DEFAULT_CATEGORY:
            raise ValueError(
                f"Income Transaction {state.transaction_id!r} has invalid default income category state"
            )
        return
    if state.default_category is not None and state.default_category not in categories:
        raise ValueError(
            f"Transaction {state.transaction_id!r} has unknown default category {state.default_category!r}"
        )
    if state.category != UNCLASSIFIED_CATEGORY and state.category not in categories:
        raise ValueError(
            f"Transaction {state.transaction_id!r} has unknown category {state.category!r}"
        )


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
