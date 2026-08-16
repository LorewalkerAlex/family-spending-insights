from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.source import SourceIdentity, SourceRecord, TransactionType

MANUAL_SOURCE_TYPE = "manual"
MANUAL_CURRENCY = "CNY"
MANUAL_RECORD_LOCATOR = "record"


def _normalized_optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainInvariantError(f"Manual {label} must be a string or None")
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True)
class ManualEvidence:
    """User-authored source facts with a permanent evidence id across explicit corrections."""

    evidence_id: str
    transaction_type: TransactionType
    transaction_date: date
    amount: Decimal
    currency: str
    description: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise DomainInvariantError("Manual evidence_id must be non-empty")
        if self.evidence_id != self.evidence_id.strip() or "\0" in self.evidence_id:
            raise DomainInvariantError("Manual evidence_id must be normalized and NUL-free")
        if self.transaction_type not in ("income", "expense"):
            raise DomainInvariantError("Manual transaction_type must be 'income' or 'expense'")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise DomainInvariantError("Manual amount must be a finite Decimal")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise DomainInvariantError("Manual currency must be non-empty")
        if self.currency != self.currency.strip().upper():
            raise DomainInvariantError("Manual currency must be normalized uppercase text")
        if self.description is not None:
            if not isinstance(self.description, str) or not self.description.strip():
                raise DomainInvariantError("Manual description must be non-empty when present")
            if self.description != self.description.strip():
                raise DomainInvariantError("Manual description must not contain surrounding whitespace")


def create_manual_evidence(
    *,
    transaction_type: TransactionType,
    transaction_date: date,
    amount: Decimal,
    description: str | None = None,
    currency: str = MANUAL_CURRENCY,
    evidence_id: str | None = None,
) -> ManualEvidence:
    """Create source-native Manual evidence; enrichment decisions belong downstream."""
    return ManualEvidence(
        evidence_id=evidence_id or f"manual_{uuid.uuid4().hex}",
        transaction_type=transaction_type,
        transaction_date=transaction_date,
        amount=amount,
        currency=currency.strip().upper(),
        description=_normalized_optional_text(description, "description"),
    )


def manual_evidence_to_source_record(evidence: ManualEvidence) -> SourceRecord:
    """Preserve the permanent evidence id while deriving one canonical SourceRecord id."""
    return SourceRecord(
        identity=SourceIdentity(
            source_type=MANUAL_SOURCE_TYPE,
            evidence_identity=evidence.evidence_id,
            record_locator=MANUAL_RECORD_LOCATOR,
        ),
        transaction_type=evidence.transaction_type,
        transaction_date=evidence.transaction_date,
        amount=evidence.amount,
        currency=evidence.currency,
        description=evidence.description,
    )
