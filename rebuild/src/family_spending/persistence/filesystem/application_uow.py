from __future__ import annotations

from dataclasses import dataclass

from family_spending.application.ports.storage import MutationScope, UnitOfWork
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.unit_of_work import FileUnitOfWork


@dataclass(frozen=True)
class FilesystemUnitOfWorkProvider:
    """Translate logical Application mutation scopes into exact filesystem participants."""

    layout: StorageLayout

    def open(self, scope: MutationScope, *, label: str) -> UnitOfWork:
        participants = {
            "source_sync": (
                self.layout.source_links,
                self.layout.enrichment_decisions,
            ),
            "manual_input": (
                self.layout.manual_evidence,
                self.layout.source_links,
                self.layout.enrichment_decisions,
            ),
            "enrichment": (self.layout.enrichment_decisions,),
            "mapping_review": (
                self.layout.merchant_mappings,
                self.layout.category_mappings,
            ),
            "schedule_rules": (
                self.layout.scheduled_rules,
                self.layout.schedule_execution,
            ),
            "scheduled_run": (
                self.layout.scheduled_rules,
                self.layout.schedule_execution,
                self.layout.manual_evidence,
                self.layout.source_links,
                self.layout.enrichment_decisions,
            ),
            "feedback": (self.layout.feedback,),
        }.get(scope)
        if participants is None:
            raise ValueError(f"Unknown Application mutation scope {scope!r}")
        return FileUnitOfWork(participants, label=label)
