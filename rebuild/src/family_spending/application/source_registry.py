from __future__ import annotations

from dataclasses import dataclass

from family_spending.application.ports.source import Source
from family_spending.domain.source import SourceRecord


class SourceRegistryError(RuntimeError):
    """Raised when registered Sources violate generic source contracts."""


@dataclass(frozen=True)
class SourceRegistry:
    """Hold configured Sources while keeping central source orchestration source-agnostic."""

    sources: tuple[Source, ...]

    def __post_init__(self) -> None:
        source_types: set[str] = set()
        for source in self.sources:
            source_type = source.source_type
            if not isinstance(source_type, str) or not source_type.strip():
                raise SourceRegistryError("Registered Source source_type must be non-empty")
            if source_type != source_type.strip():
                raise SourceRegistryError("Registered Source source_type must be normalized")
            if source_type in source_types:
                raise SourceRegistryError(f"Duplicate registered Source type: {source_type!r}")
            source_types.add(source_type)

    def load_records(self) -> tuple[SourceRecord, ...]:
        """Load all registered Sources and fail fast on source-type or identity violations."""
        records: list[SourceRecord] = []
        seen_ids: set[str] = set()
        for source in self.sources:
            for record in source.load_records():
                if record.source_type != source.source_type:
                    raise SourceRegistryError(
                        f"Source {source.source_type!r} returned record {record.id!r} "
                        f"with source_type {record.source_type!r}"
                    )
                if record.id in seen_ids:
                    raise SourceRegistryError(f"Duplicate SourceRecord id: {record.id!r}")
                seen_ids.add(record.id)
                records.append(record)
        return tuple(records)
