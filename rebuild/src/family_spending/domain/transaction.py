from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.source import SourceRecord, TransactionType

TRANSACTION_ID_DIGEST_LENGTH = 24
SourceLinkRole = Literal["authoritative", "supporting"]


@dataclass(frozen=True)
class Transaction:
    """System-level financial fact reconstructed from its authoritative SourceLink."""

    id: str
    transaction_type: TransactionType
    transaction_date: date
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise DomainInvariantError("transaction id must be a non-empty string")
        if self.transaction_type not in ("income", "expense"):
            raise DomainInvariantError("transaction_type must be 'income' or 'expense'")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise DomainInvariantError("amount must be a finite Decimal")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise DomainInvariantError("currency must be a non-empty string")
        if self.currency != self.currency.strip().upper():
            raise DomainInvariantError("currency must be normalized uppercase text")


@dataclass(frozen=True)
class SourceLink:
    """Durable identity decision linking one source fact to a stable Transaction."""

    transaction_id: str
    source_record_id: str
    role: SourceLinkRole

    def __post_init__(self) -> None:
        if not self.transaction_id.strip():
            raise DomainInvariantError("SourceLink transaction_id must not be empty")
        if not self.source_record_id.strip():
            raise DomainInvariantError("SourceLink source_record_id must not be empty")
        if self.role not in ("authoritative", "supporting"):
            raise DomainInvariantError("SourceLink role must be authoritative or supporting")


def build_transaction_id(source_record: SourceRecord) -> str:
    """Create initial Transaction identity from canonical SourceRecord identity."""
    payload = f"transaction\0{source_record.id}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:TRANSACTION_ID_DIGEST_LENGTH]
    return f"txn_{digest}"


def transaction_from_source_record(
    source_record: SourceRecord,
    *,
    transaction_id: str | None = None,
) -> Transaction:
    """Rebuild core facts while preserving a previously durable Transaction identity."""
    return Transaction(
        id=transaction_id or build_transaction_id(source_record),
        transaction_type=source_record.transaction_type,
        transaction_date=source_record.transaction_date,
        amount=source_record.amount,
        currency=source_record.currency,
    )
