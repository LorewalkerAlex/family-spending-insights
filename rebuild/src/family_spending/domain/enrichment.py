from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.mapping import (
    MappingCatalog,
    OTHER_EXPENSE_CATEGORY,
    UNCLASSIFIED_CATEGORY,
)
from family_spending.domain.source import SourceRecord
from family_spending.domain.transaction import Transaction

INCOME_DEFAULT_CATEGORY = "其他收入"
GENERAL_SHOPPING_CATEGORY = "综合购物"
HIGH_VALUE_GENERAL_SHOPPING_THRESHOLD = Decimal("1000")
OTHER_EXPENSE_REVIEW = "other_expense_review"
HIGH_VALUE_GENERAL_SHOPPING_REVIEW = "high_value_general_shopping_review"
CategorySource = Literal[
    "merchant_default",
    "transaction_override",
    "income_default",
    "unclassified",
]


def _validate_optional_decision_text(value: str | None, label: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise DomainInvariantError(f"{label} must be non-empty when present")
    if value != value.strip():
        raise DomainInvariantError(f"{label} must not contain surrounding whitespace")


@dataclass(frozen=True)
class EnrichmentDecision:
    """Sparse durable user decision; Mapping-derived values are intentionally absent."""

    transaction_id: str
    merchant_override: str | None = None
    category_override: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_id, str) or not self.transaction_id.strip():
            raise DomainInvariantError("EnrichmentDecision transaction_id must not be empty")
        _validate_optional_decision_text(self.merchant_override, "merchant_override")
        _validate_optional_decision_text(self.category_override, "category_override")
        _validate_optional_decision_text(self.note, "note")
        if self.merchant_override is None and self.category_override is None and self.note is None:
            raise DomainInvariantError("EnrichmentDecision must contain at least one durable decision")


@dataclass(frozen=True)
class ResolvedEnrichment:
    """Derived current interpretation reconstructed from source, Mapping, and user decision."""

    transaction_id: str
    merchant_name: str | None
    display_name: str
    default_category: str | None
    category: str
    category_source: CategorySource
    is_unclassified: bool
    review_signals: tuple[str, ...]
    note: str | None = None


def _validate_authoritative_facts(transaction: Transaction, source_record: SourceRecord) -> None:
    """Reject stale joins because Transaction facts must mirror the authoritative SourceRecord."""
    if (
        transaction.transaction_type != source_record.transaction_type
        or transaction.transaction_date != source_record.transaction_date
        or transaction.amount != source_record.amount
        or transaction.currency != source_record.currency
    ):
        raise DomainInvariantError(
            f"Transaction {transaction.id!r} does not match its authoritative SourceRecord facts"
        )


def resolve_enrichment(
    transaction: Transaction,
    source_record: SourceRecord,
    mappings: MappingCatalog,
    decision: EnrichmentDecision | None = None,
) -> ResolvedEnrichment:
    """Resolve current Enrichment without persisting any Mapping-derived materialized state."""
    _validate_authoritative_facts(transaction, source_record)
    if decision is not None and decision.transaction_id != transaction.id:
        raise DomainInvariantError("EnrichmentDecision belongs to a different Transaction")

    note = decision.note if decision is not None else None
    if transaction.transaction_type == "income":
        if decision is not None and (
            decision.merchant_override is not None or decision.category_override is not None
        ):
            raise DomainInvariantError("Income Enrichment supports Note decisions only")
        return ResolvedEnrichment(
            transaction_id=transaction.id,
            merchant_name=None,
            display_name=source_record.description or source_record.id,
            default_category=None,
            category=INCOME_DEFAULT_CATEGORY,
            category_source="income_default",
            is_unclassified=False,
            review_signals=(),
            note=note,
        )

    mapped_merchant = mappings.merchant_for_description(source_record.description)
    merchant = (
        decision.merchant_override
        if decision is not None and decision.merchant_override is not None
        else mapped_merchant
    )
    default_category = mappings.default_category_for_merchant(merchant)
    category_override = decision.category_override if decision is not None else None
    if category_override is not None and category_override not in mappings.categories:
        raise DomainInvariantError(
            f"Unknown category override {category_override!r} for Transaction {transaction.id!r}"
        )

    if category_override is not None:
        category = category_override
        category_source: CategorySource = "transaction_override"
    elif default_category is not None:
        category = default_category
        category_source = "merchant_default"
    else:
        category = UNCLASSIFIED_CATEGORY
        category_source = "unclassified"

    review_signals = (
        (OTHER_EXPENSE_REVIEW,)
        if category_source == "merchant_default" and default_category == OTHER_EXPENSE_CATEGORY
        else ()
    )
    return ResolvedEnrichment(
        transaction_id=transaction.id,
        merchant_name=merchant,
        display_name=merchant or source_record.description or source_record.id,
        default_category=default_category,
        category=category,
        category_source=category_source,
        is_unclassified=category_source == "unclassified",
        review_signals=review_signals,
        note=note,
    )


def consumption_review_signals(
    enrichment: ResolvedEnrichment,
    spending: Decimal,
) -> tuple[str, ...]:
    """Evaluate amount-dependent review rules after refund netting, never on raw charge amount."""
    if not isinstance(spending, Decimal) or not spending.is_finite() or spending <= Decimal("0"):
        raise DomainInvariantError(
            f"Net consumption spending must be finite and positive, got {spending!r}"
        )
    if enrichment.category_source == "transaction_override":
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
    authoritative_sources_by_transaction_id: Mapping[str, SourceRecord],
    mappings: MappingCatalog,
    decisions: tuple[EnrichmentDecision, ...] = (),
) -> tuple[ResolvedEnrichment, ...]:
    """Resolve an ordered current Enrichment view while rejecting stale sparse decisions."""
    transaction_ids: set[str] = set()
    for transaction in transactions:
        if transaction.id in transaction_ids:
            raise DomainInvariantError(f"Duplicate Transaction id {transaction.id!r}")
        transaction_ids.add(transaction.id)

    decisions_by_id: dict[str, EnrichmentDecision] = {}
    for decision in decisions:
        if decision.transaction_id in decisions_by_id:
            raise DomainInvariantError(
                f"Duplicate EnrichmentDecision for Transaction {decision.transaction_id!r}"
            )
        decisions_by_id[decision.transaction_id] = decision
    orphaned = sorted(set(decisions_by_id) - transaction_ids)
    if orphaned:
        raise DomainInvariantError(
            f"EnrichmentDecision references missing Transactions: {orphaned!r}"
        )

    resolved: list[ResolvedEnrichment] = []
    for transaction in transactions:
        try:
            source_record = authoritative_sources_by_transaction_id[transaction.id]
        except KeyError as exc:
            raise DomainInvariantError(
                f"Transaction {transaction.id!r} has no authoritative SourceRecord for Enrichment"
            ) from exc
        resolved.append(
            resolve_enrichment(
                transaction,
                source_record,
                mappings,
                decisions_by_id.get(transaction.id),
            )
        )
    return tuple(resolved)
