from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from family_spending.domain.source import SourceRecord
from family_spending.sources.manual.model import (
    MANUAL_SOURCE_TYPE,
    ManualEvidence,
    manual_evidence_to_source_record,
)


class ManualEvidenceReader(Protocol):
    """Read Manual evidence without exposing JSONL or filesystem paths to the Source."""

    def load_all(self) -> tuple[ManualEvidence, ...]: ...


@dataclass(frozen=True)
class ManualSource:
    """Adapt user-entered evidence into canonical SourceRecords."""

    evidence_reader: ManualEvidenceReader
    source_type: ClassVar[str] = MANUAL_SOURCE_TYPE

    def load_records(self) -> tuple[SourceRecord, ...]:
        return tuple(
            manual_evidence_to_source_record(evidence)
            for evidence in self.evidence_reader.load_all()
        )
