from __future__ import annotations

from typing import Protocol

from family_spending.domain.source import SourceRecord


class Source(Protocol):
    """Expose one normalized source without leaking acquisition or persistence details."""

    @property
    def source_type(self) -> str: ...

    def load_records(self) -> tuple[SourceRecord, ...]: ...
