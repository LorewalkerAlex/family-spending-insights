from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from family_spending.application.enrichment import UNSET, update_decision_collection
from family_spending.application.errors import (
    ApplicationNotFoundError,
    ApplicationStateError,
)
from family_spending.application.models import ManualInputDeletionView, ManualInputView, TransactionView
from family_spending.application.ports.runtime import MutationExecutor, RuntimeReader
from family_spending.application.ports.storage import (
    EnrichmentDecisionStore,
    UnitOfWorkProvider,
)
from family_spending.application.source_sync import SourceSyncService
from family_spending.sources.manual.model import ManualEvidence, manual_evidence_to_source_record


class ManualEvidenceRepository(Protocol):
    def load_all(self) -> tuple[ManualEvidence, ...]: ...

    def replace_all(self, records: tuple[ManualEvidence, ...]) -> None: ...


class ManualInputService:
    """Own Manual Source lifecycle plus its coordinated identity/enrichment consequences."""

    def __init__(
        self,
        *,
        evidence_store: ManualEvidenceRepository,
        enrichment_store: EnrichmentDecisionStore,
        source_sync: SourceSyncService,
        runtime: RuntimeReader,
        coordinator: MutationExecutor,
        unit_of_work_provider: UnitOfWorkProvider,
        transaction_view: Callable[[str], TransactionView],
    ) -> None:
        self._evidence = evidence_store
        self._enrichment = enrichment_store
        self._source_sync = source_sync
        self._runtime = runtime
        self._coordinator = coordinator
        self._uow = unit_of_work_provider
        self._transaction_view = transaction_view

    def create(self, evidence: ManualEvidence, *, note: object = None) -> ManualInputView:
        source_record = manual_evidence_to_source_record(evidence)

        def mutation() -> tuple[str, str]:
            current = self._evidence.load_all()
            if any(item.evidence_id == evidence.evidence_id for item in current):
                raise ApplicationStateError(
                    f"Manual evidence {evidence.evidence_id!r} already exists"
                )
            self._evidence.replace_all(current + (evidence,))
            sync_result = self._source_sync.sync_inside_mutation()
            decision = self._source_sync.decision_for_source(sync_result, source_record)
            if note is not None:
                self._enrichment.replace(
                    update_decision_collection(
                        self._enrichment.load(),
                        decision.transaction_id,
                        note=note,
                    )
                )
            return decision.transaction_id, decision.action

        transaction_id, action = self._coordinator.execute(
            label="Manual Input create",
            unit_of_work=self._uow.open("manual_input", label="Manual Input create"),
            mutation=mutation,
        )
        return ManualInputView(
            evidence_id=evidence.evidence_id,
            source_record_id=source_record.id,
            action=action,
            transaction=self._transaction_view(transaction_id),
        )

    def correct(
        self,
        evidence_id: str,
        replacement: ManualEvidence,
        *,
        note: object = UNSET,
    ) -> ManualInputView:
        """Reconsider one stable Manual Source identity because correction is an explicit lifecycle action."""
        if replacement.evidence_id != evidence_id:
            raise ApplicationStateError(
                "Manual correction must preserve the permanent evidence id"
            )
        source_record = manual_evidence_to_source_record(replacement)

        def mutation() -> tuple[str, str]:
            records = list(self._evidence.load_all())
            current_index = next(
                (
                    position
                    for position, item in enumerate(records)
                    if item.evidence_id == evidence_id
                ),
                None,
            )
            if current_index is None:
                raise ApplicationNotFoundError(
                    f"Manual evidence {evidence_id!r} does not exist"
                )
            state = self._runtime.current_state()
            previous_link = next(
                (
                    link
                    for link in state.household.source_links
                    if link.source_record_id == source_record.id
                ),
                None,
            )
            if previous_link is None:
                raise ApplicationStateError(
                    f"Manual evidence {evidence_id!r} is pending Source Sync and cannot be corrected yet"
                )
            records[current_index] = replacement
            self._evidence.replace_all(tuple(records))
            sync_result = self._source_sync.sync_inside_mutation(
                reconsider_source_record_id=source_record.id,
            )
            decision = self._source_sync.decision_for_source(sync_result, source_record)
            if note is not UNSET:
                self._enrichment.replace(
                    update_decision_collection(
                        self._enrichment.load(),
                        decision.transaction_id,
                        note=note,
                    )
                )
            return decision.transaction_id, decision.action

        transaction_id, action = self._coordinator.execute(
            label="Manual Input correction",
            unit_of_work=self._uow.open("manual_input", label="Manual Input correction"),
            mutation=mutation,
        )
        return ManualInputView(
            evidence_id=evidence_id,
            source_record_id=source_record.id,
            action=action,
            transaction=self._transaction_view(transaction_id),
        )

    def delete(self, evidence_id: str) -> ManualInputDeletionView:
        def mutation() -> tuple[str, str, bool]:
            records = self._evidence.load_all()
            current = next(
                (item for item in records if item.evidence_id == evidence_id),
                None,
            )
            if current is None:
                raise ApplicationNotFoundError(
                    f"Manual evidence {evidence_id!r} does not exist"
                )
            source_record = manual_evidence_to_source_record(current)
            state = self._runtime.current_state()
            previous_link = next(
                (
                    link
                    for link in state.household.source_links
                    if link.source_record_id == source_record.id
                ),
                None,
            )
            if previous_link is None:
                raise ApplicationStateError(
                    f"Manual evidence {evidence_id!r} is pending Source Sync and cannot be deleted through this lifecycle"
                )
            self._evidence.replace_all(
                tuple(item for item in records if item.evidence_id != evidence_id)
            )
            sync_result = self._source_sync.sync_inside_mutation()
            surviving_transaction_ids = {
                decision.transaction_id for decision in sync_result.decisions
            }
            return (
                source_record.id,
                previous_link.transaction_id,
                previous_link.transaction_id not in surviving_transaction_ids,
            )

        source_record_id, transaction_id, transaction_removed = self._coordinator.execute(
            label="Manual Input deletion",
            unit_of_work=self._uow.open("manual_input", label="Manual Input deletion"),
            mutation=mutation,
        )
        return ManualInputDeletionView(
            evidence_id=evidence_id,
            source_record_id=source_record_id,
            transaction_id=transaction_id,
            transaction_removed=transaction_removed,
        )
