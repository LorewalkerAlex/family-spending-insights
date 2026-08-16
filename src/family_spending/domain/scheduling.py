from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.source import TransactionType

SCHEDULE_OCCURRENCE_DIGEST_LENGTH = 24
ScheduleAction = Literal["created", "matched", "reused", "recovered"]


@dataclass(frozen=True)
class ScheduledRule:
    """Durable monthly configuration independent of execution cursor state."""

    id: str
    enabled: bool
    transaction_type: TransactionType
    amount: Decimal
    description: str
    first_occurrence_date: date
    currency: str = "CNY"
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise DomainInvariantError("ScheduledRule id must not be empty")
        if not isinstance(self.enabled, bool):
            raise DomainInvariantError("ScheduledRule enabled must be a boolean")
        if self.transaction_type not in ("income", "expense"):
            raise DomainInvariantError("ScheduledRule transaction_type is invalid")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise DomainInvariantError("ScheduledRule amount must be a finite Decimal")
        if not isinstance(self.description, str) or not self.description.strip():
            raise DomainInvariantError("ScheduledRule description must not be empty")
        if self.description != self.description.strip():
            raise DomainInvariantError("ScheduledRule description must be normalized")
        if self.first_occurrence_date.day > 28:
            raise DomainInvariantError("Monthly ScheduledRule occurrence day must be 1-28")
        if not isinstance(self.currency, str) or self.currency != self.currency.strip().upper():
            raise DomainInvariantError("ScheduledRule currency must be normalized uppercase text")
        if self.note is not None and (not self.note.strip() or self.note != self.note.strip()):
            raise DomainInvariantError("ScheduledRule note must be normalized non-empty text")


@dataclass(frozen=True)
class ScheduleExecutionState:
    """Operational cursor and last-run metadata split from user-owned rule configuration."""

    rule_id: str
    last_processed_occurrence_date: date | None = None
    last_source_record_id: str | None = None
    last_transaction_id: str | None = None
    last_action: ScheduleAction | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise DomainInvariantError("ScheduleExecutionState rule_id must not be empty")
        metadata = (self.last_source_record_id, self.last_transaction_id, self.last_action)
        if any(value is None for value in metadata) and any(value is not None for value in metadata):
            raise DomainInvariantError(
                "Schedule execution last source, transaction, and action must be all present or all absent"
            )
        if any(value is not None for value in metadata) and self.last_processed_occurrence_date is None:
            raise DomainInvariantError("Schedule execution metadata requires a processed occurrence date")
        for value, label in (
            (self.last_source_record_id, "last_source_record_id"),
            (self.last_transaction_id, "last_transaction_id"),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise DomainInvariantError(f"Schedule execution {label} must be non-empty")
        if self.last_action is not None and self.last_action not in (
            "created",
            "matched",
            "reused",
            "recovered",
        ):
            raise DomainInvariantError(f"Invalid Schedule execution action {self.last_action!r}")


def next_monthly_date(value: date) -> date:
    """Advance one calendar month while preserving the V1-safe recurrence day."""
    if value.day > 28:
        raise DomainInvariantError("Monthly recurrence day must be 1-28")
    if value.month == 12:
        return date(value.year + 1, 1, value.day)
    return date(value.year, value.month + 1, value.day)


def next_occurrence_date(
    rule: ScheduledRule,
    execution: ScheduleExecutionState | None,
) -> date:
    """Derive the next occurrence from durable rule configuration plus an optional cursor."""
    if execution is None or execution.last_processed_occurrence_date is None:
        return rule.first_occurrence_date
    if execution.rule_id != rule.id:
        raise DomainInvariantError("Schedule execution state belongs to a different rule")

    candidate = rule.first_occurrence_date
    while candidate <= execution.last_processed_occurrence_date:
        candidate = next_monthly_date(candidate)
    return candidate


def scheduled_occurrence_identity(rule_id: str, occurrence_date: date) -> str:
    """Keep generated Manual evidence idempotent across retries and cursor recovery."""
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise DomainInvariantError("rule_id must not be empty")
    payload = f"scheduled-occurrence\0{rule_id}\0{occurrence_date.isoformat()}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:SCHEDULE_OCCURRENCE_DIGEST_LENGTH]
    return f"schedule_occ_{digest}"
