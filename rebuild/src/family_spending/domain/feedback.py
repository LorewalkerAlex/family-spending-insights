from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Literal

from family_spending.domain.errors import DomainInvariantError

FeedbackStatus = Literal["open", "resolved"]
FeedbackRuntime = Literal["desktop_web", "mini_h5", "weapp"]
FEEDBACK_STATUSES = frozenset({"open", "resolved"})
FEEDBACK_RUNTIMES = frozenset({"desktop_web", "mini_h5", "weapp"})


def _normalized_optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainInvariantError(f"Feedback {label} must be a string or None")
    stripped = value.strip()
    return stripped or None


@dataclass(frozen=True)
class FeedbackContext:
    """Small optional product context attached to user feedback."""

    runtime: FeedbackRuntime | None = None
    page: str | None = None
    workspace: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None

    def __post_init__(self) -> None:
        if self.runtime is not None and self.runtime not in FEEDBACK_RUNTIMES:
            raise DomainInvariantError(f"Feedback runtime is invalid: {self.runtime!r}")
        for field in ("page", "workspace", "entity_type", "entity_id"):
            value = getattr(self, field)
            normalized = _normalized_optional_text(value, field)
            object.__setattr__(self, field, normalized)
        if (self.entity_type is None) != (self.entity_id is None):
            raise DomainInvariantError(
                "Feedback entity_type and entity_id must be provided together"
            )


@dataclass(frozen=True)
class FeedbackItem:
    """Durable local product-feedback record independent of issue-tracker state."""

    id: str
    created_at: datetime
    status: FeedbackStatus
    content: str
    context: FeedbackContext

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip() or self.id != self.id.strip():
            raise DomainInvariantError("Feedback id must be normalized non-empty text")
        if not isinstance(self.content, str) or not self.content.strip():
            raise DomainInvariantError("Feedback content must be non-empty text")
        if self.content != self.content.strip():
            raise DomainInvariantError("Feedback content must not contain surrounding whitespace")
        if self.status not in FEEDBACK_STATUSES:
            raise DomainInvariantError(f"Feedback status is invalid: {self.status!r}")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise DomainInvariantError("Feedback created_at must include a timezone")
        object.__setattr__(self, "created_at", self.created_at.astimezone(timezone.utc))


def update_feedback_status(item: FeedbackItem, status: FeedbackStatus) -> FeedbackItem:
    """Resolve or reopen feedback without changing its identity, content, or capture context."""
    if status not in FEEDBACK_STATUSES:
        raise DomainInvariantError(f"Feedback status is invalid: {status!r}")
    return replace(item, status=status)
