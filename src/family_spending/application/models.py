from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from family_spending.domain.enrichment import ResolvedEnrichment
from family_spending.domain.source import SourceRecord
from family_spending.domain.transaction import Transaction


@dataclass(frozen=True)
class TransactionView:
    """Application-level joined Transaction view shared by HTTP and CLI adapters."""

    transaction: Transaction
    source_record: SourceRecord
    enrichment: ResolvedEnrichment
    review_signals: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.transaction.id,
            "type": self.transaction.transaction_type,
            "date": self.transaction.transaction_date.isoformat(),
            "amount": format(self.transaction.amount, "f"),
            "currency": self.transaction.currency,
            "source": {
                "id": self.source_record.id,
                "type": self.source_record.source_type,
                "description": self.source_record.description,
            },
            "enrichment": {
                "merchant": self.enrichment.merchant_name,
                "display_name": self.enrichment.display_name,
                "default_category": self.enrichment.default_category,
                "category": self.enrichment.category,
                "category_source": self.enrichment.category_source,
                "note": self.enrichment.note,
                "is_unclassified": self.enrichment.is_unclassified,
                "review_signals": list(self.review_signals),
            },
        }


@dataclass(frozen=True)
class ManualInputView:
    evidence_id: str
    source_record_id: str
    action: str
    transaction: TransactionView

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_record_id": self.source_record_id,
            "action": self.action,
            "transaction": self.transaction.to_dict(),
        }


@dataclass(frozen=True)
class ManualInputRecordView:
    """Manual evidence view derived from the same immutable Runtime generation as Transaction state."""

    evidence_id: str
    source_record: SourceRecord
    transaction_id: str
    source_role: str
    transaction: TransactionView

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_record_id": self.source_record.id,
            "transaction_id": self.transaction_id,
            "source_role": self.source_role,
            "type": self.source_record.transaction_type,
            "date": self.source_record.transaction_date.isoformat(),
            "amount": format(self.source_record.amount, "f"),
            "currency": self.source_record.currency,
            "description": self.source_record.description,
            "transaction": self.transaction.to_dict(),
        }


@dataclass(frozen=True)
class ManualInputDeletionView:
    evidence_id: str
    source_record_id: str
    transaction_id: str
    transaction_removed: bool


@dataclass(frozen=True)
class MappingReviewItem:
    description: str
    transaction_count: int
    total_amount: Decimal
    currency: str
    latest_date: date
    source_types: tuple[str, ...]
    transaction_only_exception_count: int


@dataclass(frozen=True)
class MerchantMappingOption:
    name: str
    default_category: str


@dataclass(frozen=True)
class MappingReviewPreview:
    token: str
    description: str
    merchant: str
    category: str
    is_new_merchant: bool
    previous_default_category: str | None
    description_transaction_count: int
    description_affected_transaction_count: int
    default_category_affected_transaction_count: int
    total_affected_transaction_count: int
    preserved_merchant_exception_count: int
    preserved_category_exception_count: int


@dataclass(frozen=True)
class MappingReviewWorkspaceView:
    items: tuple[MappingReviewItem, ...]
    merchants: tuple[MerchantMappingOption, ...]
    categories: tuple[str, ...]


@dataclass(frozen=True)
class ScheduledInputRuleView:
    """Transport-neutral scheduled rule plus its current execution view."""

    id: str
    enabled: bool
    transaction_type: str
    amount: Decimal
    currency: str
    description: str
    note: str | None
    next_date: date
    last_occurrence_date: date | None
    last_source_record_id: str | None
    last_transaction_id: str | None
    last_action: str | None


@dataclass(frozen=True)
class ScheduledInputOccurrence:
    rule_id: str
    occurrence_date: date
    evidence_id: str
    source_record_id: str
    transaction_id: str
    action: str


@dataclass(frozen=True)
class ScheduledInputRunResult:
    occurrences: tuple[ScheduledInputOccurrence, ...]
