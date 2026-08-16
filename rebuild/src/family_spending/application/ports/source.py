from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from family_spending.domain.source import SourceRecord


class Source(Protocol):
    """Expose one normalized source without leaking acquisition or persistence details."""

    @property
    def source_type(self) -> str: ...

    def load_records(self) -> tuple[SourceRecord, ...]: ...


@dataclass(frozen=True)
class SourceAcquisitionResult:
    """Report one external acquisition pass without exposing source-specific payloads."""

    source_type: str
    fetched_count: int
    added_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.source_type, str) or not self.source_type.strip():
            raise ValueError("SourceAcquisitionResult source_type must be non-empty")
        if self.source_type != self.source_type.strip():
            raise ValueError("SourceAcquisitionResult source_type must be normalized")
        for count in (self.fetched_count, self.added_count):
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError("Source acquisition counts must be integers")
            if count < 0:
                raise ValueError("Source acquisition counts must be non-negative")
        if self.added_count > self.fetched_count:
            raise ValueError("Source acquisition added_count cannot exceed fetched_count")


class SourceAcquirer(Protocol):
    """Acquire durable Source Evidence while leaving reconciliation to Application Source Sync."""

    @property
    def source_type(self) -> str: ...

    def acquire(self) -> SourceAcquisitionResult: ...
