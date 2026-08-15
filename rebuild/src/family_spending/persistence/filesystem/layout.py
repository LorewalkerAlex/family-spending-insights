from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageLayout:
    """Derive every canonical filesystem location from one absolute household data root."""

    data_root: Path

    def __post_init__(self) -> None:
        root = Path(self.data_root).expanduser()
        if not root.is_absolute():
            raise ValueError("StorageLayout data_root must be absolute")
        object.__setattr__(self, "data_root", root)

    @property
    def manifest(self) -> Path:
        return self.data_root / "manifest.json"

    @property
    def evidence_root(self) -> Path:
        return self.data_root / "evidence"

    @property
    def cmb_email_evidence(self) -> Path:
        return self.evidence_root / "cmb-email"

    @property
    def manual_evidence(self) -> Path:
        return self.evidence_root / "manual" / "records.jsonl"

    @property
    def state_root(self) -> Path:
        return self.data_root / "state"

    @property
    def source_links(self) -> Path:
        return self.state_root / "identity" / "source-links.jsonl"

    @property
    def enrichment_decisions(self) -> Path:
        return self.state_root / "enrichment" / "decisions.jsonl"

    @property
    def merchant_mappings(self) -> Path:
        return self.state_root / "mappings" / "merchants.yaml"

    @property
    def category_mappings(self) -> Path:
        return self.state_root / "mappings" / "categories.yaml"

    @property
    def scheduled_rules(self) -> Path:
        return self.state_root / "schedules" / "rules.json"

    @property
    def schedule_execution(self) -> Path:
        return self.state_root / "schedules" / "execution.json"

    @property
    def feedback(self) -> Path:
        return self.state_root / "feedback" / "feedback.jsonl"

    @property
    def derived_root(self) -> Path:
        return self.data_root / "derived"

    @property
    def derived_sources(self) -> Path:
        return self.derived_root / "sources"

    @property
    def derived_projections(self) -> Path:
        return self.derived_root / "projections"

    @property
    def derived_indexes(self) -> Path:
        return self.derived_root / "indexes"

    @property
    def managed_directories(self) -> tuple[Path, ...]:
        """Return directories bootstrap may create without inventing any data files."""
        return (
            self.cmb_email_evidence,
            self.manual_evidence.parent,
            self.source_links.parent,
            self.enrichment_decisions.parent,
            self.merchant_mappings.parent,
            self.scheduled_rules.parent,
            self.feedback.parent,
            self.derived_sources,
            self.derived_projections,
            self.derived_indexes,
        )
