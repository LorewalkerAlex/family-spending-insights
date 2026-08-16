from __future__ import annotations

from dataclasses import dataclass

from family_spending.application.enrichment import EnrichmentCommandService
from family_spending.application.feedback import FeedbackService
from family_spending.application.manual_input import ManualInputService
from family_spending.application.mapping_review import MappingReviewService
from family_spending.application.queries import QueryService
from family_spending.application.scheduling import ScheduledInputService
from family_spending.application.service import FamilySpendingApplication
from family_spending.application.source_registry import SourceRegistry
from family_spending.application.source_sync import SourceSyncService
from family_spending.config import AppConfig
from family_spending.domain.reconciliation import ReconciliationEngine
from family_spending.persistence.filesystem.application_uow import FilesystemUnitOfWorkProvider
from family_spending.persistence.filesystem.cmb_email_evidence_store import CmbEmailEvidenceStore
from family_spending.persistence.filesystem.enrichment_store import FilesystemEnrichmentDecisionStore
from family_spending.persistence.filesystem.feedback_store import FilesystemFeedbackStore
from family_spending.persistence.filesystem.identity_store import FilesystemIdentityStore
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.manifest import initialize_storage
from family_spending.persistence.filesystem.manual_evidence_store import ManualEvidenceStore
from family_spending.persistence.filesystem.mapping_store import FilesystemMappingStore
from family_spending.persistence.filesystem.schedule_store import FilesystemScheduleStore
from family_spending.runtime.coordinator import MutationCoordinator
from family_spending.runtime.state import RuntimeSnapshotBuilder, RuntimeStore
from family_spending.sources.cmb_email.acquisition import CmbEmailAcquirer
from family_spending.sources.cmb_email.connector import CmbEmailConnector
from family_spending.sources.cmb_email.reconciliation import CmbEmailReconciliationPolicy
from family_spending.sources.cmb_email.source import CmbEmailSource
from family_spending.sources.manual.reconciliation import ManualReconciliationPolicy
from family_spending.sources.manual.source import ManualSource


@dataclass(frozen=True)
class RuntimeComponents:
    """Canonical composition root outputs reused by Runtime, Application, and later interfaces."""

    config: AppConfig
    layout: StorageLayout
    cmb_evidence_store: CmbEmailEvidenceStore
    manual_evidence_store: ManualEvidenceStore
    identity_store: FilesystemIdentityStore
    mapping_store: FilesystemMappingStore
    enrichment_store: FilesystemEnrichmentDecisionStore
    schedule_store: FilesystemScheduleStore
    feedback_store: FilesystemFeedbackStore
    unit_of_work_provider: FilesystemUnitOfWorkProvider
    source_registry: SourceRegistry
    reconciliation_engine: ReconciliationEngine
    snapshot_builder: RuntimeSnapshotBuilder
    runtime: RuntimeStore
    coordinator: MutationCoordinator
    application: FamilySpendingApplication

    def build_cmb_acquirer(self, connector: CmbEmailConnector) -> CmbEmailAcquirer:
        """Wire optional external acquisition without making core runtime depend on IMAP details."""
        if not self.config.sources.cmb_email.enabled:
            raise RuntimeError("CMB email acquisition is disabled by runtime configuration")
        return CmbEmailAcquirer(connector, self.cmb_evidence_store)


def compose_runtime(config: AppConfig) -> RuntimeComponents:
    """Build canonical Runtime and Application exclusively from configured persistent state."""
    layout = StorageLayout(config.storage.data_root)
    initialize_storage(layout)

    cmb_store = CmbEmailEvidenceStore(layout)
    manual_store = ManualEvidenceStore(layout)
    identity_store = FilesystemIdentityStore(layout)
    mapping_store = FilesystemMappingStore(layout)
    enrichment_store = FilesystemEnrichmentDecisionStore(layout)
    schedule_store = FilesystemScheduleStore(layout)
    feedback_store = FilesystemFeedbackStore(layout)
    uow_provider = FilesystemUnitOfWorkProvider(layout)

    # Historical evidence remains household truth even when external CMB polling is disabled.
    cmb_source = CmbEmailSource(cmb_store)
    manual_source = ManualSource(manual_store)
    registry = SourceRegistry((cmb_source, manual_source))
    reconciliation_engine = ReconciliationEngine(
        (
            CmbEmailReconciliationPolicy(),
            ManualReconciliationPolicy(),
        )
    )
    builder = RuntimeSnapshotBuilder(
        source_registry=registry,
        identity_store=identity_store,
        mapping_store=mapping_store,
        enrichment_store=enrichment_store,
        statement_date_provider=cmb_source,
    )
    runtime = RuntimeStore()
    runtime.bootstrap(builder.build())
    coordinator = MutationCoordinator(runtime, builder)

    source_sync = SourceSyncService(
        source_registry=registry,
        reconciliation_engine=reconciliation_engine,
        identity_store=identity_store,
        mapping_store=mapping_store,
        enrichment_store=enrichment_store,
        runtime=runtime,
        coordinator=coordinator,
        unit_of_work_provider=uow_provider,
    )
    queries = QueryService(runtime=runtime)
    manual_input = ManualInputService(
        evidence_store=manual_store,
        enrichment_store=enrichment_store,
        source_sync=source_sync,
        runtime=runtime,
        coordinator=coordinator,
        unit_of_work_provider=uow_provider,
        transaction_view=queries.get_transaction,
    )
    enrichment = EnrichmentCommandService(
        decision_store=enrichment_store,
        runtime=runtime,
        coordinator=coordinator,
        unit_of_work_provider=uow_provider,
        transaction_view=queries.get_transaction,
    )
    mapping_review = MappingReviewService(
        mapping_store=mapping_store,
        runtime=runtime,
        coordinator=coordinator,
        unit_of_work_provider=uow_provider,
    )
    scheduling = ScheduledInputService(
        schedule_store=schedule_store,
        manual_evidence_store=manual_store,
        enrichment_store=enrichment_store,
        identity_store=identity_store,
        source_sync=source_sync,
        runtime=runtime,
        coordinator=coordinator,
        unit_of_work_provider=uow_provider,
    )
    feedback = FeedbackService(
        store=feedback_store,
        coordinator=coordinator,
        unit_of_work_provider=uow_provider,
    )
    application = FamilySpendingApplication(
        source_sync=source_sync,
        queries=queries,
        manual_input=manual_input,
        enrichment=enrichment,
        mapping_review=mapping_review,
        scheduling=scheduling,
        feedback=feedback,
    )

    return RuntimeComponents(
        config=config,
        layout=layout,
        cmb_evidence_store=cmb_store,
        manual_evidence_store=manual_store,
        identity_store=identity_store,
        mapping_store=mapping_store,
        enrichment_store=enrichment_store,
        schedule_store=schedule_store,
        feedback_store=feedback_store,
        unit_of_work_provider=uow_provider,
        source_registry=registry,
        reconciliation_engine=reconciliation_engine,
        snapshot_builder=builder,
        runtime=runtime,
        coordinator=coordinator,
        application=application,
    )
