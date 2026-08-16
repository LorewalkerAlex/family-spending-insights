from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from family_spending.application.errors import ApplicationConflictError, ApplicationStateError
from family_spending.application.ports.runtime import MutationExecutor, RuntimeReader
from family_spending.application.ports.storage import (
    EnrichmentDecisionStore,
    IdentityStore,
    MappingStore,
    UnitOfWorkProvider,
)
from family_spending.application.source_registry import SourceRegistry
from family_spending.domain.reconciliation import (
    ReconciliationDecision,
    ReconciliationEngine,
    ReconciliationError,
    ReconciliationHints,
)
from family_spending.domain.source import SourceRecord


@dataclass(frozen=True)
class SourceSyncResult:
    """Application result describing identity actions from one Source Sync."""

    source_record_count: int
    transaction_count: int
    created_count: int
    matched_count: int
    reused_count: int
    decisions: tuple[ReconciliationDecision, ...]


class SourceSyncService:
    """Own the only generic SourceRecord-to-SourceLink synchronization path."""

    def __init__(
        self,
        *,
        source_registry: SourceRegistry,
        reconciliation_engine: ReconciliationEngine,
        identity_store: IdentityStore,
        mapping_store: MappingStore,
        enrichment_store: EnrichmentDecisionStore,
        runtime: RuntimeReader,
        coordinator: MutationExecutor,
        unit_of_work_provider: UnitOfWorkProvider,
    ) -> None:
        self._source_registry = source_registry
        self._engine = reconciliation_engine
        self._identity_store = identity_store
        self._mapping_store = mapping_store
        self._enrichment_store = enrichment_store
        self._runtime = runtime
        self._coordinator = coordinator
        self._uow = unit_of_work_provider

    def sync(self) -> SourceSyncResult:
        """Reconcile all current SourceRecords under the process-wide single-writer boundary."""
        return self._coordinator.execute(
            label="Source Sync",
            unit_of_work=self._uow.open("source_sync", label="Source Sync"),
            mutation=self.sync_inside_mutation,
        )

    def sync_inside_mutation(
        self,
        *,
        reconsider_source_record_id: str | None = None,
    ) -> SourceSyncResult:
        """Apply SourceLink changes inside an already-owned coordinator/UoW mutation."""
        records = self._source_registry.load_records()
        record_ids = {record.id for record in records}
        existing_links = tuple(
            link
            for link in self._identity_store.load()
            if link.source_record_id in record_ids
        )
        try:
            existing_links = self._engine.recover_authority_after_source_removal(
                records, existing_links
            )
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc

        mappings = self._mapping_store.load()
        current_state = self._runtime.current_state()
        source_merchants = {
            record.id: (
                mappings.merchant_for_description(record.description)
                if record.transaction_type == "expense"
                else None
            )
            for record in records
        }
        transaction_merchants = {
            transaction_id: enrichment.merchant_name
            for transaction_id, enrichment in current_state.indexes.enrichment_by_transaction_id.items()
        }
        hints = ReconciliationHints(
            merchant_by_transaction_id=transaction_merchants,
            merchant_by_source_record_id=source_merchants,
        )
        try:
            if reconsider_source_record_id is None:
                result = self._engine.reconcile(
                    records,
                    existing_links=existing_links,
                    hints=hints,
                )
            else:
                result = self._engine.reconcile_reconsidered_source(
                    records,
                    existing_links=existing_links,
                    source_record_id=reconsider_source_record_id,
                    hints=hints,
                )
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except Exception as exc:
            raise ApplicationStateError(f"Unable to reconcile current Sources: {exc}") from exc

        self._identity_store.replace(result.source_links)
        transaction_ids = {transaction.id for transaction in result.transactions}
        current_decisions = self._enrichment_store.load()
        retained_decisions = tuple(
            decision
            for decision in current_decisions
            if decision.transaction_id in transaction_ids
        )
        if retained_decisions != current_decisions:
            self._enrichment_store.replace(retained_decisions)

        counts = Counter(decision.action for decision in result.decisions)
        return SourceSyncResult(
            source_record_count=len(records),
            transaction_count=len(result.transactions),
            created_count=counts["created"],
            matched_count=counts["matched"],
            reused_count=counts["reused"],
            decisions=result.decisions,
        )

    @staticmethod
    def decision_for_source(
        result: SourceSyncResult,
        source_record: SourceRecord,
    ) -> ReconciliationDecision:
        decision = next(
            (
                item
                for item in result.decisions
                if item.source_record_id == source_record.id
            ),
            None,
        )
        if decision is None:
            raise ApplicationStateError(
                f"Source Sync omitted SourceRecord {source_record.id!r} from its decision set"
            )
        return decision
