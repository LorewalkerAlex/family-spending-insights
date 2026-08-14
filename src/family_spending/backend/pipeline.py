from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from family_spending.backend.paths import BackendPaths
from family_spending.backend.state import (
    CurrentHouseholdSnapshot,
    load_current_household_snapshot,
)
from family_spending.enrichment import (
    INCOME_DEFAULT_CATEGORY,
    TransactionEnrichmentState,
    materialize_enrichment_state,
    update_merchant_enrichment_state,
    update_note_enrichment_state,
)
from family_spending.enrichment_store import (
    read_enrichment_states,
    write_enrichment_states,
)
from family_spending.ingestion.cmb_email_transactions import read_transactions_csv
from family_spending.infrastructure.file_uow import (
    FileUnitOfWork,
    FileUnitOfWorkRollbackError,
)
from family_spending.manual_source import ManualSourceEntry, read_manual_source_entries
from family_spending.mapping import load_merchant_mappings
from family_spending.reconciliation import ReconciliationDecision
from family_spending.source_link_store import (
    read_transaction_source_links,
    write_transaction_source_links,
)
from family_spending.spending_projection import (
    SpendingProjection,
    build_spending_projection,
    persist_spending_projection,
    write_spending_projection,
)
from family_spending.transaction_resolution import build_household_domain_state
from family_spending.transactions import Transaction, TransactionSourceLink


class HouseholdPipelineRollbackError(RuntimeError):
    """Raised when a failed full sync cannot restore every persisted participant."""


@dataclass(frozen=True)
class HouseholdSyncSummary:
    """Report the externally useful counters produced by one full Source sync."""

    raw_transactions: int
    zero_amount_transactions: int
    refund_transactions: int
    same_merchant_refund_matches: int
    same_merchant_matched_amount: Decimal
    net_consumption_transactions: int
    fully_refunded_transactions: int
    partially_refunded_transactions: int
    unmatched_refund_count: int
    unmatched_refund_amount: Decimal
    unclassified_net_transactions: int
    months: int
    total_net_spending: Decimal
    shown_months: int
    shown_net_spending: Decimal


@dataclass(frozen=True)
class ProjectionRebuildSummary:
    """Describe a downstream-only rebuild without implying Source/Reconciliation work."""

    transactions: int
    months: int
    total_net_spending: Decimal
    shown_months: int
    shown_net_spending: Decimal


@dataclass(frozen=True)
class HouseholdSourceSyncPlan:
    """Hold one fully evaluated Source sync before coordinated persistence begins."""

    raw_transaction_count: int
    decisions: tuple[ReconciliationDecision, ...]
    source_links: tuple[TransactionSourceLink, ...]
    enrichment_states: tuple[TransactionEnrichmentState, ...]
    projection: SpendingProjection
    summary: HouseholdSyncSummary


@dataclass(frozen=True)
class ManualSourceReplacement:
    """Describe correction semantics that must be applied inside one Source-sync plan."""

    previous_entry: ManualSourceEntry
    replacement_entry: ManualSourceEntry
    previous_link: TransactionSourceLink
    update_note: bool


def _normalize_legacy_income_states(
    transactions: tuple[Transaction, ...],
    states: tuple[TransactionEnrichmentState, ...],
) -> tuple[TransactionEnrichmentState, ...]:
    """Migrate only implicit pre-Income states while preserving explicit historical decisions."""
    transactions_by_id = {transaction.id: transaction for transaction in transactions}
    normalized: list[TransactionEnrichmentState] = []
    for state in states:
        transaction = transactions_by_id[state.transaction_id]
        if (
            transaction.transaction_type == "income"
            and state.category_source in ("merchant_default", "unclassified")
        ):
            state = TransactionEnrichmentState(
                transaction_id=state.transaction_id,
                merchant_name=None,
                default_category=None,
                category=INCOME_DEFAULT_CATEGORY,
                category_source="income_default",
                note=state.note,
            )
        normalized.append(state)
    return tuple(normalized)


def _sync_summary(
    raw_transaction_count: int,
    projection: SpendingProjection,
) -> HouseholdSyncSummary:
    summary = projection.summary
    return HouseholdSyncSummary(
        raw_transactions=raw_transaction_count,
        zero_amount_transactions=summary.zero_amount_transactions,
        refund_transactions=summary.refund_transactions,
        same_merchant_refund_matches=summary.same_merchant_refund_matches,
        same_merchant_matched_amount=summary.same_merchant_matched_amount,
        net_consumption_transactions=summary.net_consumption_transactions,
        fully_refunded_transactions=summary.fully_refunded_transactions,
        partially_refunded_transactions=summary.partially_refunded_transactions,
        unmatched_refund_count=summary.unmatched_refund_count,
        unmatched_refund_amount=summary.unmatched_refund_amount,
        unclassified_net_transactions=summary.unclassified_net_transactions,
        months=summary.months,
        total_net_spending=summary.total_net_spending,
        shown_months=summary.shown_months,
        shown_net_spending=summary.shown_net_spending,
    )


class HouseholdPipeline:
    """Own explicit backend processing stages instead of hiding them in feature modules."""

    def __init__(self, paths: BackendPaths | None = None) -> None:
        self.paths = paths or BackendPaths()

    def plan_source_sync(
        self,
        *,
        manual_entries: tuple[ManualSourceEntry, ...] | None = None,
        submitted_source_ids: tuple[str, ...] = (),
        manual_replacement: ManualSourceReplacement | None = None,
    ) -> HouseholdSourceSyncPlan:
        """Evaluate Source/Reconciliation/Enrichment/Projection once without writing files.

        `submitted_source_ids` identifies explicit Manual creates whose non-null Note must
        update current Enrichment even when reconciliation matched an existing Transaction.
        `manual_replacement` preserves correction identity and explicit Note semantics inside
        the same Source-sync planning boundary.
        """
        raw_transactions = read_transactions_csv(self.paths.transactions)
        if manual_entries is None:
            manual_entries = read_manual_source_entries(self.paths.manual_source)
        existing_links = read_transaction_source_links(self.paths.source_links)
        existing_enrichment_states = read_enrichment_states(self.paths.enrichment_state)
        mappings = load_merchant_mappings(
            self.paths.merchants,
            self.paths.categories,
        )
        existing_enrichment_by_id = {
            item.transaction_id: item for item in existing_enrichment_states
        }
        state = build_household_domain_state(
            raw_transactions,
            manual_entries,
            mappings,
            existing_links=existing_links,
            existing_enrichment_states=existing_enrichment_by_id,
        )

        preserved_replacement_identity = False
        if manual_replacement is not None:
            replacement = manual_replacement
            if replacement.previous_link not in existing_links:
                raise ValueError(
                    "Manual replacement previous link is not part of current Source Link state"
                )
            provisional_decision = next(
                (
                    item
                    for item in state.reconciliation.decisions
                    if item.source_record_id == replacement.replacement_entry.id
                ),
                None,
            )
            if provisional_decision is None:
                raise ValueError(
                    "Manual replacement Source was not present in reconciliation decisions: "
                    f"{replacement.replacement_entry.id!r}"
                )
            if (
                replacement.previous_link.role == "authoritative"
                and replacement.previous_link.transaction_id
                not in state.transactions_by_id
                and provisional_decision.action == "created"
            ):
                remapped_links = tuple(
                    TransactionSourceLink(
                        transaction_id=item.transaction_id,
                        source_record_id=(
                            replacement.replacement_entry.id
                            if item.source_record_id == replacement.previous_entry.id
                            else item.source_record_id
                        ),
                        role=item.role,
                    )
                    for item in existing_links
                )
                state = build_household_domain_state(
                    raw_transactions,
                    manual_entries,
                    mappings,
                    existing_links=remapped_links,
                    existing_enrichment_states=existing_enrichment_by_id,
                )
                preserved_replacement_identity = True

        enrichment_states = _normalize_legacy_income_states(
            state.reconciliation.transactions,
            state.enrichment_states,
        )
        entries_by_id = {entry.id: entry for entry in manual_entries}
        decisions_by_source = {
            decision.source_record_id: decision
            for decision in state.reconciliation.decisions
        }
        states_by_id = {
            item.transaction_id: item for item in enrichment_states
        }

        for source_record_id in submitted_source_ids:
            entry = entries_by_id.get(source_record_id)
            decision = decisions_by_source.get(source_record_id)
            if entry is None or decision is None:
                raise ValueError(
                    "Submitted Manual Source must exist in the evaluated reconciliation state: "
                    f"{source_record_id!r}"
                )
            if entry.note is None:
                continue
            states_by_id[decision.transaction_id] = update_note_enrichment_state(
                states_by_id[decision.transaction_id],
                entry.note,
            )

        if manual_replacement is not None:
            replacement = manual_replacement
            decision = decisions_by_source.get(replacement.replacement_entry.id)
            if decision is None:
                raise ValueError(
                    "Manual replacement Source must exist in the final reconciliation state: "
                    f"{replacement.replacement_entry.id!r}"
                )
            corrected_state = states_by_id[decision.transaction_id]
            if preserved_replacement_identity:
                previous_mapping_merchant = (
                    mappings.description_to_merchant.get(
                        replacement.previous_entry.description
                    )
                    if replacement.previous_entry.description is not None
                    else None
                )
                if corrected_state.merchant_name == previous_mapping_merchant:
                    replacement_merchant = (
                        mappings.description_to_merchant.get(
                            replacement.replacement_entry.description
                        )
                        if replacement.replacement_entry.description is not None
                        else None
                    )
                    replacement_default = (
                        mappings.merchant_to_category.get(replacement_merchant)
                        if replacement_merchant is not None
                        else None
                    )
                    corrected_state = update_merchant_enrichment_state(
                        corrected_state,
                        merchant_name=replacement_merchant,
                        default_category=replacement_default,
                    )
            if replacement.update_note:
                corrected_state = update_note_enrichment_state(
                    corrected_state,
                    replacement.replacement_entry.note,
                )
            states_by_id[decision.transaction_id] = corrected_state

        enrichment_states = tuple(
            states_by_id[transaction.id]
            for transaction in state.reconciliation.transactions
        )
        enrichment_states_by_id = {
            item.transaction_id: item for item in enrichment_states
        }
        enrichments_by_id = MappingProxyType(
            {
                transaction.id: materialize_enrichment_state(
                    enrichment_states_by_id[transaction.id],
                    state.source_records_by_transaction_id[transaction.id],
                )
                for transaction in state.reconciliation.transactions
            }
        )
        projection = build_spending_projection(
            state.reconciliation.transactions,
            state.transactions_by_id,
            state.source_records_by_transaction_id,
            enrichments_by_id,
            self.paths.emails,
        )
        summary = _sync_summary(len(raw_transactions), projection)
        return HouseholdSourceSyncPlan(
            raw_transaction_count=len(raw_transactions),
            decisions=state.reconciliation.decisions,
            source_links=state.reconciliation.source_links,
            enrichment_states=enrichment_states,
            projection=projection,
            summary=summary,
        )

    def source_sync_persisted_paths(self) -> tuple[Path, ...]:
        """Expose the files written by one Source sync for a larger owning unit of work."""
        return (
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.paths.financial_summary,
        )

    def write_source_sync_plan(self, plan: HouseholdSourceSyncPlan) -> None:
        """Write an evaluated plan without opening a transaction; caller owns the UoW."""
        write_transaction_source_links(
            plan.source_links,
            self.paths.source_links,
        )
        write_enrichment_states(
            plan.enrichment_states,
            self.paths.enrichment_state,
        )
        persist_spending_projection(
            plan.projection,
            self.paths.spending_statistics,
            financial_output_path=self.paths.financial_summary,
        )

    def sync_sources(self) -> HouseholdSyncSummary:
        """Run Source -> Reconciliation -> Enrichment -> Projection and commit it atomically."""
        plan = self.plan_source_sync()
        try:
            with FileUnitOfWork(
                self.source_sync_persisted_paths(),
                label="Household Source sync",
            ) as unit_of_work:
                self.write_source_sync_plan(plan)
                unit_of_work.commit()
        except FileUnitOfWorkRollbackError as exc:
            raise HouseholdPipelineRollbackError(str(exc)) from exc
        return plan.summary

    def load_current_state(self) -> CurrentHouseholdSnapshot:
        """Load the already-reconciled state used by queries and downstream-only pipelines."""
        return load_current_household_snapshot(self.paths)

    def rebuild_projections(self) -> ProjectionRebuildSummary:
        """Rebuild Analytics/Projection from current state without rerunning Reconciliation."""
        snapshot = self.load_current_state()
        projection = build_spending_projection(
            snapshot.transactions,
            snapshot.transactions_by_id,
            snapshot.source_records_by_transaction_id,
            snapshot.enrichments_by_transaction_id,
            self.paths.emails,
        )
        write_spending_projection(
            projection,
            self.paths.spending_statistics,
            financial_output_path=self.paths.financial_summary,
        )
        summary = projection.summary
        return ProjectionRebuildSummary(
            transactions=len(snapshot.transactions),
            months=summary.months,
            total_net_spending=summary.total_net_spending,
            shown_months=summary.shown_months,
            shown_net_spending=summary.shown_net_spending,
        )
