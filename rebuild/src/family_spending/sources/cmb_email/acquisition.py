from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Protocol

from family_spending.application.ports.source import SourceAcquisitionResult
from family_spending.sources.cmb_email.connector import CmbEmailConnector
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence
from family_spending.sources.cmb_email.parser import CMB_SOURCE_TYPE


class CmbEmailEvidenceWriter(Protocol):
    """Persist immutable CMB evidence without exposing its concrete filesystem store."""

    def add(self, evidence: CmbEmailEvidence) -> bool: ...


@dataclass(frozen=True)
class CmbEmailAcquirer:
    """Acquire mailbox bytes and publish only immutable raw evidence; no identity decisions occur here."""

    connector: CmbEmailConnector
    evidence_writer: CmbEmailEvidenceWriter
    source_type: ClassVar[str] = CMB_SOURCE_TYPE

    def acquire(self) -> SourceAcquisitionResult:
        raw_messages = self.connector.fetch_raw_messages()
        added = 0
        for raw_bytes in raw_messages:
            if self.evidence_writer.add(CmbEmailEvidence(raw_bytes)):
                added += 1
        return SourceAcquisitionResult(
            source_type=self.source_type,
            fetched_count=len(raw_messages),
            added_count=added,
        )
