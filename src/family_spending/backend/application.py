from __future__ import annotations

from datetime import date

from family_spending.application import (
    ApplicationConflictError,
    ApplicationNotFoundError,
    ApplicationPaths,
    ApplicationStateError,
    ApplicationValidationError,
    FamilySpendingApplication,
    ManualInputCorrectionView,
    ManualInputDeletionView,
    ManualInputRecordView,
    ManualInputView,
    TransactionView,
)
from family_spending.backend.manual_commands import (
    ManualInputCommandRollbackError,
    ManualInputCommandService,
)
from family_spending.backend.paths import BackendPaths
from family_spending.backend.runtime import (
    BackendRuntime,
    BackendRuntimeNotReadyError,
)
from family_spending.backend.scheduled_jobs import ScheduledInputJobRunner
from family_spending.backend.state import BackendStateError, CurrentHouseholdSnapshot
from family_spending.enrichment import (
    update_category_enrichment_state,
    update_merchant_enrichment_state,
    update_note_enrichment_state,
    validate_enrichment_state_categories,
)
from family_spending.enrichment_store import write_enrichment_states
from family_spending.infrastructure.file_uow import (
    FileUnitOfWork,
    FileUnitOfWorkRollbackError,
)
from family_spending.manual_source import (
    ManualSourceDataError,
    create_manual_source_entry,
)
from family_spending.mapping import load_merchant_mappings
from family_spending.mapping_review import (
    MappingReviewError,
    MappingReviewPreview,
    write_mapping_review,
)
from family_spending.reconciliation import ReconciliationError
from family_spending.scheduled_input import ScheduledInputError, ScheduledInputRunResult
from family_spending.settings import FINANCIAL_SUMMARY_FILE
from family_spending.spending_projection import (
    build_spending_projection,
    persist_spending_projection,
)


_UNSET = object()


class RuntimeFamilySpendingApplication(FamilySpendingApplication):
    """Bridge the existing use-case surface onto one reusable BackendRuntime snapshot.

    The legacy Application remains available as a compatibility boundary while this class
    moves query-facing state and coordinated mutations onto the new backend architecture.
    """

    def __init__(
        self,
        paths: ApplicationPaths | None = None,
        *,
        runtime: BackendRuntime | None = None,
    ) -> None:
        super().__init__(paths)
        backend_paths = BackendPaths(
            transactions=self.paths.transactions,
            manual_source=self.paths.manual_source,
            source_links=self.paths.source_links,
            enrichment_state=self.paths.enrichment_state,
            merchants=self.paths.merchants,
            categories=self.paths.categories,
            spending_statistics=self.paths.spending_statistics,
            financial_summary=self.paths.spending_statistics.with_name(
                FINANCIAL_SUMMARY_FILE.name
            ),
            emails=self.paths.emails,
        )
        if runtime is not None and runtime.paths != backend_paths:
            raise ValueError(
                "RuntimeFamilySpendingApplication runtime paths must match ApplicationPaths"
            )
        self.runtime = runtime or BackendRuntime(backend_paths)
        self.backend_paths = backend_paths
        self._manual_commands = ManualInputCommandService(self.runtime)
        self._scheduled_job_runner = ScheduledInputJobRunner(
            self.runtime,
            self._scheduled_rules_path,
        )

    def initialize(self) -> None:
        """Bootstrap one runtime snapshot, then run due jobs against the same persisted backend."""
        self.runtime.bootstrap()
        self.run_due_scheduled_inputs()

    def run_due_scheduled_inputs(
        self,
        as_of: date | None = None,
    ) -> ScheduledInputRunResult:
        """Catch up scheduled Manual Sources through one runtime-owned Source sync."""
        try:
            return self._scheduled_job_runner.run_due(as_of or date.today())
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except ScheduledInputError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def _load_snapshot(self) -> CurrentHouseholdSnapshot:
        """Serve legacy queries from BackendRuntime instead of rebuilding per request."""
        try:
            try:
                return self.runtime.current_state()
            except BackendRuntimeNotReadyError:
                return self.runtime.refresh()
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def list_categories(self) -> tuple[str, ...]:
        """Read configured categories from the reusable runtime Mapping snapshot."""
        return tuple(sorted(self._load_snapshot().mappings.categories))

    def list_manual_descriptions(self) -> tuple[str, ...]:
        """Reuse cached Manual Source entries for source-native description suggestions."""
        seen: set[str] = set()
        descriptions: list[str] = []
        for entry in reversed(self._load_snapshot().manual_entries):
            if entry.description is None or entry.description in seen:
                continue
            seen.add(entry.description)
            descriptions.append(entry.description)
        return tuple(descriptions)

    def list_manual_inputs(self) -> tuple[ManualInputRecordView, ...]:
        """Join Manual Source facts to current Transactions without re-reading persisted stores."""
        snapshot = self._load_snapshot()
        links_by_source = {
            link.source_record_id: link
            for link in snapshot.source_links
        }
        views: list[ManualInputRecordView] = []
        for entry in reversed(snapshot.manual_entries):
            link = links_by_source.get(entry.id)
            if link is None:
                raise ApplicationStateError(
                    f"Manual source record {entry.id!r} has no current Transaction link; "
                    "run backend sync"
                )
            transaction = snapshot.transactions_by_id.get(link.transaction_id)
            if transaction is None:
                raise ApplicationStateError(
                    f"Manual source record {entry.id!r} links to missing Transaction "
                    f"{link.transaction_id!r}"
                )
            views.append(
                ManualInputRecordView(
                    entry=entry,
                    transaction_id=link.transaction_id,
                    source_role=link.role,
                    transaction=self._view(snapshot, transaction),
                )
            )
        return tuple(views)

    def create_manual_input(
        self,
        *,
        transaction_type: object,
        transaction_date: object,
        amount: object,
        description: object,
        note: object = None,
    ) -> ManualInputView:
        """Create one Manual Source through the runtime-owned Source-sync command boundary."""
        type_value, parsed_date, parsed_amount, description_value = self._manual_source_values(
            transaction_type=transaction_type,
            transaction_date=transaction_date,
            amount=amount,
            description=description,
        )
        note_value = self._optional_text(note, "note")
        entry = create_manual_source_entry(
            transaction_type=type_value,
            transaction_date=parsed_date,
            amount=parsed_amount,
            description=description_value,
            note=note_value,
        )
        try:
            result = self._manual_commands.create(entry)
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except ManualSourceDataError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except ManualInputCommandRollbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return ManualInputView(
            source_record_id=result.source_record_id,
            action=result.action,
            transaction=self.get_transaction(result.transaction_id),
        )

    def correct_manual_input(
        self,
        source_record_id: str,
        *,
        transaction_type: object,
        transaction_date: object,
        amount: object,
        description: object,
        note: object = _UNSET,
    ) -> ManualInputCorrectionView:
        """Replace one Manual Source identity through the runtime-owned Source-sync boundary."""
        snapshot = self._load_snapshot()
        current = next(
            (
                entry
                for entry in snapshot.manual_entries
                if entry.id == source_record_id
            ),
            None,
        )
        if current is None:
            raise ApplicationNotFoundError(
                f"Manual source record {source_record_id!r} does not exist"
            )
        type_value, parsed_date, parsed_amount, description_value = self._manual_source_values(
            transaction_type=transaction_type,
            transaction_date=transaction_date,
            amount=amount,
            description=description,
        )
        update_note = note is not _UNSET
        note_value = (
            current.note
            if not update_note
            else self._optional_text(note, "note")
        )
        replacement = create_manual_source_entry(
            transaction_type=type_value,
            transaction_date=parsed_date,
            amount=parsed_amount,
            description=description_value,
            merchant_name=current.merchant_name,
            category=current.category,
            note=note_value,
            currency=current.currency,
        )
        try:
            result = self._manual_commands.correct(
                source_record_id,
                replacement,
                update_note=update_note,
            )
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except ManualSourceDataError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except ManualInputCommandRollbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return ManualInputCorrectionView(
            replaced_source_record_id=source_record_id,
            manual_input=ManualInputView(
                source_record_id=result.source_record_id,
                action=result.action,
                transaction=self.get_transaction(result.transaction_id),
            ),
        )

    def delete_manual_input(self, source_record_id: str) -> ManualInputDeletionView:
        """Delete one Manual Source through the runtime-owned Source-sync command boundary."""
        if not any(
            entry.id == source_record_id
            for entry in self._load_snapshot().manual_entries
        ):
            raise ApplicationNotFoundError(
                f"Manual source record {source_record_id!r} does not exist"
            )
        try:
            result = self._manual_commands.delete(source_record_id)
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except ManualSourceDataError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except ManualInputCommandRollbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return ManualInputDeletionView(
            source_record_id=result.source_record_id,
            transaction_id=result.transaction_id,
            transaction_removed=result.transaction_removed,
        )

    def apply_mapping_review(
        self,
        *,
        description: object,
        merchant: object,
        category: object,
        preview_token: object,
        confirm_new_merchant: object = False,
    ) -> MappingReviewPreview:
        """Commit Mapping, Enrichment, and both projections through one shared file UoW."""
        if not isinstance(confirm_new_merchant, bool):
            raise ApplicationValidationError("confirm_new_merchant must be a boolean")
        token = self._required_text(preview_token, "preview_token")
        snapshot = self._load_snapshot()
        plan = self._plan_mapping_review(
            snapshot,
            description=description,
            merchant=merchant,
            category=category,
        )
        if plan.preview.token != token:
            raise ApplicationConflictError(
                "Mapping Review state changed after preview; refresh the preview before applying"
            )
        if plan.preview.is_new_merchant and not confirm_new_merchant:
            raise ApplicationValidationError(
                "Creating a new Merchant requires explicit confirm_new_merchant=true"
            )

        persisted_paths = (
            self.paths.merchants,
            self.paths.categories,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.backend_paths.financial_summary,
        )
        try:
            with FileUnitOfWork(
                persisted_paths,
                label="Mapping Review mutation",
            ) as unit_of_work:
                write_mapping_review(
                    merchants_path=self.paths.merchants,
                    categories_path=self.paths.categories,
                    description=plan.preview.description,
                    merchant=plan.preview.merchant,
                    category=plan.preview.category,
                )
                refreshed_mappings = load_merchant_mappings(
                    self.paths.merchants,
                    self.paths.categories,
                )
                for state in plan.enrichment_states:
                    validate_enrichment_state_categories(
                        state,
                        refreshed_mappings.categories,
                    )
                enrichments_by_id = self._materialize_enrichments(
                    snapshot,
                    plan.enrichment_states,
                )
                projection = build_spending_projection(
                    snapshot.transactions,
                    snapshot.transactions_by_id,
                    snapshot.source_records_by_transaction_id,
                    enrichments_by_id,
                    self.paths.emails,
                )
                write_enrichment_states(
                    plan.enrichment_states,
                    self.paths.enrichment_state,
                )
                persist_spending_projection(
                    projection,
                    self.paths.spending_statistics,
                    financial_output_path=self.backend_paths.financial_summary,
                )
                self.runtime.refresh()
                unit_of_work.commit()
        except MappingReviewError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        except FileUnitOfWorkRollbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return plan.preview

    def update_enrichment(
        self,
        transaction_id: str,
        *,
        merchant: str | None | object = _UNSET,
        category: str | None | object = _UNSET,
        note: str | None | object = _UNSET,
    ) -> TransactionView:
        """Commit Enrichment and both projections together without rebuilding Reconciliation."""
        if merchant is _UNSET and category is _UNSET and note is _UNSET:
            raise ApplicationValidationError(
                "Enrichment update requires at least one of merchant, category, or note"
            )
        snapshot = self._load_snapshot()
        try:
            current = snapshot.enrichment_states_by_transaction_id[transaction_id]
        except KeyError as exc:
            raise ApplicationNotFoundError(
                f"Transaction {transaction_id!r} does not exist"
            ) from exc

        updated = current
        if merchant is not _UNSET:
            merchant_name = self._optional_text(merchant, "merchant")
            default_category = (
                snapshot.mappings.merchant_to_category.get(merchant_name)
                if merchant_name is not None
                else None
            )
            updated = update_merchant_enrichment_state(
                updated,
                merchant_name=merchant_name,
                default_category=default_category,
            )
        if category is not _UNSET:
            category_name = self._optional_text(category, "category")
            if (
                category_name is not None
                and category_name not in snapshot.mappings.categories
            ):
                raise ApplicationValidationError(
                    f"Unknown category {category_name!r}; use one of the formal "
                    "configured categories"
                )
            updated = update_category_enrichment_state(updated, category_name)
        if note is not _UNSET:
            updated = update_note_enrichment_state(
                updated,
                self._optional_text(note, "note"),
            )
        try:
            validate_enrichment_state_categories(
                updated,
                snapshot.mappings.categories,
            )
        except ValueError as exc:
            raise ApplicationValidationError(str(exc)) from exc

        states = tuple(
            updated if state.transaction_id == transaction_id else state
            for state in snapshot.enrichment_states
        )
        enrichments_by_id = self._materialize_enrichments(snapshot, states)
        projection = build_spending_projection(
            snapshot.transactions,
            snapshot.transactions_by_id,
            snapshot.source_records_by_transaction_id,
            enrichments_by_id,
            self.paths.emails,
        )

        persisted_paths = (
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.backend_paths.financial_summary,
        )
        try:
            with FileUnitOfWork(
                persisted_paths,
                label="Enrichment mutation",
            ) as unit_of_work:
                write_enrichment_states(states, self.paths.enrichment_state)
                persist_spending_projection(
                    projection,
                    self.paths.spending_statistics,
                    financial_output_path=self.backend_paths.financial_summary,
                )
                self.runtime.refresh()
                unit_of_work.commit()
        except FileUnitOfWorkRollbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc

        refreshed = self._load_snapshot()
        transaction = refreshed.transactions_by_id[transaction_id]
        return self._view(refreshed, transaction)
