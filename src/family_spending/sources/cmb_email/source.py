from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import ClassVar, Protocol

from family_spending.domain.source import SourceRecord
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence
from family_spending.sources.cmb_email.parser import CMB_SOURCE_TYPE, parse_cmb_email


class CmbEmailEvidenceReader(Protocol):
    """Read immutable CMB evidence without exposing its filesystem representation."""

    def load_all(self) -> tuple[CmbEmailEvidence, ...]: ...


@dataclass(frozen=True)
class CmbEmailSource:
    """Normalize stored CMB EML evidence and expose statement metadata needed downstream."""

    evidence_reader: CmbEmailEvidenceReader
    source_type: ClassVar[str] = CMB_SOURCE_TYPE

    def load_records(self) -> tuple[SourceRecord, ...]:
        records: list[SourceRecord] = []
        for evidence in self.evidence_reader.load_all():
            records.extend(parse_cmb_email(evidence).records)
        return tuple(records)

    def load_statement_dates_by_evidence(self) -> Mapping[str, date]:
        """Derive statement metadata from immutable EML content, keyed by evidence identity."""
        return MappingProxyType(
            {
                evidence.identity: parse_cmb_email(evidence).statement_date
                for evidence in self.evidence_reader.load_all()
            }
        )
