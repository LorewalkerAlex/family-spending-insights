from __future__ import annotations

from types import TracebackType
from typing import Literal, Protocol

from family_spending.domain.enrichment import EnrichmentDecision
from family_spending.domain.feedback import FeedbackItem
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.scheduling import ScheduleExecutionState, ScheduledRule
from family_spending.domain.transaction import SourceLink

MutationScope = Literal[
    "source_sync",
    "manual_input",
    "enrichment",
    "mapping_review",
    "schedule_rules",
    "scheduled_run",
    "feedback",
]


class IdentityStore(Protocol):
    """Persist durable SourceRecord-to-Transaction identity decisions."""

    def load(self) -> tuple[SourceLink, ...]: ...

    def replace(self, links: tuple[SourceLink, ...]) -> None: ...


class MappingStore(Protocol):
    """Persist reviewed household Mapping knowledge behind a format-neutral contract."""

    def load(self) -> MappingCatalog: ...

    def replace(self, mappings: MappingCatalog) -> None: ...


class EnrichmentDecisionStore(Protocol):
    """Persist only sparse user decisions, never resolved Mapping output."""

    def load(self) -> tuple[EnrichmentDecision, ...]: ...

    def replace(self, decisions: tuple[EnrichmentDecision, ...]) -> None: ...


class ScheduleStore(Protocol):
    """Keep user rules and execution cursor state in separate durable collections."""

    def load_rules(self) -> tuple[ScheduledRule, ...]: ...

    def replace_rules(self, rules: tuple[ScheduledRule, ...]) -> None: ...

    def load_execution(self) -> tuple[ScheduleExecutionState, ...]: ...

    def replace_execution(self, states: tuple[ScheduleExecutionState, ...]) -> None: ...


class FeedbackStore(Protocol):
    """Persist durable local product feedback independently of financial projections."""

    def load(self) -> tuple[FeedbackItem, ...]: ...

    def replace(self, items: tuple[FeedbackItem, ...]) -> None: ...


class UnitOfWork(Protocol):
    """Expose one commit boundary without leaking concrete filesystem mechanics inward."""

    def __enter__(self) -> UnitOfWork: ...

    def commit(self) -> None: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class UnitOfWorkProvider(Protocol):
    """Map logical Application mutation scopes to concrete commit boundaries."""

    def open(self, scope: MutationScope, *, label: str) -> UnitOfWork: ...
