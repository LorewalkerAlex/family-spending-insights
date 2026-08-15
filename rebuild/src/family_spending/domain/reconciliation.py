from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from family_spending.domain.transaction import SourceLink, Transaction

ReconciliationAction = Literal["created", "reused", "matched"]


@dataclass(frozen=True)
class ReconciliationEvidence:
    """Independent evidence signals kept inspectable instead of collapsing into one score."""

    source_identity_match: bool = False
    amount_match: bool | None = None
    date_distance_days: int | None = None
    merchant_match: bool | None = None


@dataclass(frozen=True)
class ReconciliationDecision:
    """Explain how one SourceRecord was assigned to Transaction identity."""

    source_record_id: str
    transaction_id: str
    action: ReconciliationAction
    evidence: ReconciliationEvidence


@dataclass(frozen=True)
class ReconciliationResult:
    """Pure result contract consumed by later source-specific reconciliation engines."""

    transactions: tuple[Transaction, ...]
    source_links: tuple[SourceLink, ...]
    decisions: tuple[ReconciliationDecision, ...]
