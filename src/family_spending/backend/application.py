from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from family_spending.backend.manual_commands import (
    ManualInputCommandRollbackError,
    ManualInputCommandService,
)
from family_spending.backend.paths import BackendPaths
from family_spending.backend.projection_queries import (
    ProjectionQueryError,
    read_financial_summary_projection,
    read_spending_statistics_projection,
)
from family_spending.backend.runtime import (
    BackendRuntime,
    BackendRuntimeNotReadyError,
)
from family_spending.backend.scheduled_jobs import ScheduledInputJobRunner
from family_spending.backend.state import BackendStateError, CurrentHouseholdSnapshot
from family_spending.enrichment import (
    TransactionEnrichment,
    TransactionEnrichmentState,
    materialize_enrichment_state,
    update_category_enrichment_state,
    update_merchant_enrichment_state,
    update_note_enrichment_state,
    validate_enrichment_state_categories,
)
from family_spending.enrichment_store import write_enrichment_states
from family_spending.feedback import (
    FEEDBACK_RUNTIMES,
    FeedbackContext,
    FeedbackError,
    FeedbackItem,
    create_feedback_item,
    read_feedback_items,
    update_feedback_status,
    write_feedback_items,
)
from family_spending.infrastructure.file_uow import (
    FileUnitOfWork,
    FileUnitOfWorkRollbackError,
)
from family_spending.manual_source import (
    ManualSourceDataError,
    ManualSourceEntry,
    create_manual_source_entry,
)
from family_spending.mapping import load_merchant_mappings
from family_spending.mapping_review import (
    MappingReviewError,
    MappingReviewItem,
    MappingReviewPlan,
    MappingReviewPreview,
    MerchantMappingOption,
    build_mapping_review_items,
    build_merchant_mapping_options,
    plan_mapping_review,
    write_mapping_review,
)
from family_spending.reconciliation import ReconciliationError
from family_spending.scheduled_input import (
    ScheduledInputError,
    ScheduledInputRule,
    ScheduledInputRunResult,
    create_scheduled_input_rule,
    read_scheduled_input_rules,
    update_scheduled_input_rule,
    write_scheduled_input_rules,
)
from family_spending.source_records import SourceRecord
from family_spending.spending_projection import (
    build_spending_projection,
    persist_spending_projection,
)
from family_spending.transactions import Transaction


_UNSET = object()


class ApplicationError(RuntimeError):
    """Base error for local Application/API use cases."""


class ApplicationNotFoundError(ApplicationError):
    """Raised when a requested current entity does not exist."""


class ApplicationValidationError(ApplicationError):
    """Raised when client input cannot be represented by the current domain model."""


class ApplicationConflictError(ApplicationError):
    """Raised when a valid command cannot be applied uniquely to current state."""


class ApplicationStateError(ApplicationError):
    """Raised when persisted backend state is unavailable or inconsistent."""


@dataclass(frozen=True)
class TransactionView:
    transaction: Transaction
    source_record: SourceRecord[Any]
    enrichment: TransactionEnrichment

    def to_dict(self) -> dict[str, Any]:
        transaction = self.transaction
        source_record = self.source_record
        enrichment = self.enrichment
        return {
            "id": transaction.id,
            "type": transaction.transaction_type,
            "date": transaction.transaction_date.isoformat(),
            "amount": format(transaction.amount, "f"),
            "currency": transaction.currency,
            "source": {
                "id": source_record.id,
                "type": source_record.source_type,
                "description": source_record.description,
            },
            "enrichment": {
                "merchant": enrichment.merchant_name,
                "display_name": enrichment.display_name,
                "default_category": enrichment.default_category,
                "category": enrichment.category,
                "category_source": enrichment.category_source,
                "note": enrichment.note,
                "is_unclassified": enrichment.is_unclassified,
                "review_signals": list(enrichment.review_signals),
            },
        }


@dataclass(frozen=True)
class ManualInputView:
    source_record_id: str
    action: str
    transaction: TransactionView

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_record_id": self.source_record_id,
            "action": self.action,
            "transaction": self.transaction.to_dict(),
        }


@dataclass(frozen=True)
class ManualInputRecordView:
    entry: ManualSourceEntry
    transaction_id: str
    source_role: str
    transaction: TransactionView

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_record_id": self.entry.id,
            "transaction_id": self.transaction_id,
            "source_role": self.source_role,
            "type": self.entry.transaction_type,
            "date": self.entry.transaction_date.isoformat(),
            "amount": format(self.entry.amount, "f"),
            "currency": self.entry.currency,
            "description": self.entry.description,
            "note": self.entry.note,
            "transaction": self.transaction.to_dict(),
        }


@dataclass(frozen=True)
class ManualInputCorrectionView:
    replaced_source_record_id: str
    manual_input: ManualInputView

    def to_dict(self) -> dict[str, Any]:
        return {
            "replaced_source_record_id": self.replaced_source_record_id,
            "manual_input": self.manual_input.to_dict(),
        }


@dataclass(frozen=True)
class ManualInputDeletionView:
    source_record_id: str
    transaction_id: str
    transaction_removed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_record_id": self.source_record_id,
            "transaction_id": self.transaction_id,
            "transaction_removed": self.transaction_removed,
        }


@dataclass(frozen=True)
class MappingReviewWorkspaceView:
    items: tuple[MappingReviewItem, ...]
    merchants: tuple[MerchantMappingOption, ...]
    categories: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "merchants": [merchant.to_dict() for merchant in self.merchants],
            "categories": list(self.categories),
        }


class FamilySpendingApplication:
    """Canonical local application boundary backed by one reusable BackendRuntime."""

    def __init__(
        self,
        paths: BackendPaths | None = None,
        *,
        runtime: BackendRuntime | None = None,
    ) -> None:
        self.paths = paths or BackendPaths()
        if runtime is not None and runtime.paths != self.paths:
            raise ValueError(
                "FamilySpendingApplication runtime paths must match BackendPaths"
            )
        self.runtime = runtime or BackendRuntime(self.paths)
        self._manual_commands = ManualInputCommandService(self.runtime)
        assert self.paths.financial_summary is not None
        assert self.paths.scheduled_input_rules is not None
        assert self.paths.feedback is not None
        self._scheduled_job_runner = ScheduledInputJobRunner(
            self.runtime,
            self.paths.scheduled_input_rules,
        )

    def initialize(self) -> None:
        """Synchronize current Source state, then materialize every due schedule."""
        self.runtime.bootstrap()
        self.run_due_scheduled_inputs()

    def _load_snapshot(self) -> CurrentHouseholdSnapshot:
        try:
            try:
                return self.runtime.current_state()
            except BackendRuntimeNotReadyError:
                return self.runtime.refresh()
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def get_financial_summary(self) -> dict[str, Any]:
        try:
            return read_financial_summary_projection(self.paths.financial_summary)
        except ProjectionQueryError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def get_spending_statistics(self) -> dict[str, Any]:
        try:
            return read_spending_statistics_projection(self.paths.spending_statistics)
        except ProjectionQueryError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def list_categories(self) -> tuple[str, ...]:
        return tuple(sorted(self._load_snapshot().mappings.categories))

    def list_feedback(self) -> tuple[FeedbackItem, ...]:
        assert self.paths.feedback is not None
        try:
            items = read_feedback_items(self.paths.feedback)
        except FeedbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return tuple(reversed(items))

    def create_feedback(
        self,
        *,
        content: object,
        context: object = None,
    ) -> FeedbackItem:
        assert self.paths.feedback is not None
        content_value = self._required_text(content, "content")
        context_value = self._feedback_context(context)
        try:
            items = read_feedback_items(self.paths.feedback)
            item = create_feedback_item(content=content_value, context=context_value)
            write_feedback_items(items + (item,), self.paths.feedback)
        except FeedbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return item

    def update_feedback(
        self,
        feedback_id: str,
        *,
        status: object,
    ) -> FeedbackItem:
        assert self.paths.feedback is not None
        feedback_id_value = self._required_text(feedback_id, "feedback_id")
        status_value = self._required_text(status, "status")
        if status_value not in {"open", "resolved"}:
            raise ApplicationValidationError(
                "Feedback status must be either 'open' or 'resolved'"
            )
        try:
            items = read_feedback_items(self.paths.feedback)
        except FeedbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        current = next((item for item in items if item.id == feedback_id_value), None)
        if current is None:
            raise ApplicationNotFoundError(
                f"Feedback {feedback_id_value!r} does not exist"
            )
        if current.status == status_value:
            return current
        try:
            updated = update_feedback_status(current, status_value)
            write_feedback_items(
                tuple(
                    updated if item.id == feedback_id_value else item
                    for item in items
                ),
                self.paths.feedback,
            )
        except FeedbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return updated

    def list_manual_descriptions(self) -> tuple[str, ...]:
        seen: set[str] = set()
        descriptions: list[str] = []
        for entry in reversed(self._load_snapshot().manual_entries):
            if entry.description is None or entry.description in seen:
                continue
            seen.add(entry.description)
            descriptions.append(entry.description)
        return tuple(descriptions)

    def list_manual_inputs(self) -> tuple[ManualInputRecordView, ...]:
        snapshot = self._load_snapshot()
        links_by_source = {
            link.source_record_id: link for link in snapshot.source_links
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
        type_value, parsed_date, parsed_amount, description_value = (
            self._manual_source_values(
                transaction_type=transaction_type,
                transaction_date=transaction_date,
                amount=amount,
                description=description,
            )
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
        except (
            ManualSourceDataError,
            ManualInputCommandRollbackError,
            BackendStateError,
        ) as exc:
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
        snapshot = self._load_snapshot()
        current = next(
            (entry for entry in snapshot.manual_entries if entry.id == source_record_id),
            None,
        )
        if current is None:
            raise ApplicationNotFoundError(
                f"Manual source record {source_record_id!r} does not exist"
            )
        type_value, parsed_date, parsed_amount, description_value = (
            self._manual_source_values(
                transaction_type=transaction_type,
                transaction_date=transaction_date,
                amount=amount,
                description=description,
            )
        )
        update_note = note is not _UNSET
        note_value = current.note if not update_note else self._optional_text(note, "note")
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
        except (
            ManualSourceDataError,
            ManualInputCommandRollbackError,
            BackendStateError,
        ) as exc:
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
        if not any(
            entry.id == source_record_id for entry in self._load_snapshot().manual_entries
        ):
            raise ApplicationNotFoundError(
                f"Manual source record {source_record_id!r} does not exist"
            )
        try:
            result = self._manual_commands.delete(source_record_id)
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except (
            ManualSourceDataError,
            ManualInputCommandRollbackError,
            BackendStateError,
        ) as exc:
            raise ApplicationStateError(str(exc)) from exc
        return ManualInputDeletionView(
            source_record_id=result.source_record_id,
            transaction_id=result.transaction_id,
            transaction_removed=result.transaction_removed,
        )

    def list_scheduled_inputs(self) -> tuple[ScheduledInputRule, ...]:
        assert self.paths.scheduled_input_rules is not None
        try:
            return read_scheduled_input_rules(self.paths.scheduled_input_rules)
        except ScheduledInputError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def create_scheduled_input(
        self,
        *,
        transaction_type: object,
        amount: object,
        description: object,
        next_date: object,
        note: object = None,
        enabled: object = True,
    ) -> ScheduledInputRule:
        type_value, parsed_amount, description_value = self._scheduled_rule_values(
            transaction_type=transaction_type,
            amount=amount,
            description=description,
        )
        next_date_value = self._scheduled_date(next_date)
        enabled_value = self._boolean(enabled, "enabled")
        note_value = self._optional_text(note, "note")
        try:
            rule = create_scheduled_input_rule(
                transaction_type=type_value,
                amount=parsed_amount,
                description=description_value,
                next_date=next_date_value,
                note=note_value,
                enabled=enabled_value,
            )
        except ScheduledInputError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        self._write_rules_and_run_due(self.list_scheduled_inputs() + (rule,))
        return self._get_scheduled_input_rule(rule.id)

    def update_scheduled_input(
        self,
        rule_id: str,
        *,
        transaction_type: object,
        amount: object,
        description: object,
        next_date: object,
        note: object = None,
        enabled: object = True,
    ) -> ScheduledInputRule:
        rules = self.list_scheduled_inputs()
        current = next((rule for rule in rules if rule.id == rule_id), None)
        if current is None:
            raise ApplicationNotFoundError(
                f"Scheduled Input rule {rule_id!r} does not exist"
            )
        type_value, parsed_amount, description_value = self._scheduled_rule_values(
            transaction_type=transaction_type,
            amount=amount,
            description=description,
        )
        try:
            updated = update_scheduled_input_rule(
                current,
                transaction_type=type_value,
                amount=parsed_amount,
                description=description_value,
                next_date=self._scheduled_date(next_date),
                note=self._optional_text(note, "note"),
                enabled=self._boolean(enabled, "enabled"),
            )
        except ScheduledInputError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        candidate = tuple(updated if rule.id == rule_id else rule for rule in rules)
        self._write_rules_and_run_due(candidate)
        return self._get_scheduled_input_rule(rule_id)

    def delete_scheduled_input(self, rule_id: str) -> ScheduledInputRule:
        assert self.paths.scheduled_input_rules is not None
        rules = self.list_scheduled_inputs()
        current = next((rule for rule in rules if rule.id == rule_id), None)
        if current is None:
            raise ApplicationNotFoundError(
                f"Scheduled Input rule {rule_id!r} does not exist"
            )
        try:
            write_scheduled_input_rules(
                tuple(rule for rule in rules if rule.id != rule_id),
                self.paths.scheduled_input_rules,
            )
        except ScheduledInputError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return current

    def run_due_scheduled_inputs(
        self,
        as_of: date | None = None,
    ) -> ScheduledInputRunResult:
        try:
            return self._scheduled_job_runner.run_due(as_of or date.today())
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except (ScheduledInputError, BackendStateError) as exc:
            raise ApplicationStateError(str(exc)) from exc

    def get_mapping_review_workspace(self) -> MappingReviewWorkspaceView:
        snapshot = self._load_snapshot()
        try:
            items = build_mapping_review_items(
                snapshot.transactions,
                snapshot.source_records_by_transaction_id,
                snapshot.enrichment_states_by_transaction_id,
                snapshot.mappings,
            )
        except MappingReviewError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return MappingReviewWorkspaceView(
            items=items,
            merchants=build_merchant_mapping_options(snapshot.mappings),
            categories=tuple(sorted(snapshot.mappings.categories)),
        )

    def preview_mapping_review(
        self,
        *,
        description: object,
        merchant: object,
        category: object,
    ) -> MappingReviewPreview:
        snapshot = self._load_snapshot()
        return self._plan_mapping_review(
            snapshot,
            description=description,
            merchant=merchant,
            category=category,
        ).preview

    def apply_mapping_review(
        self,
        *,
        description: object,
        merchant: object,
        category: object,
        preview_token: object,
        confirm_new_merchant: object = False,
    ) -> MappingReviewPreview:
        if not isinstance(confirm_new_merchant, bool):
            raise ApplicationValidationError(
                "confirm_new_merchant must be a boolean"
            )
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
            self.paths.financial_summary,
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
                    financial_output_path=self.paths.financial_summary,
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

    def list_transactions(self) -> tuple[TransactionView, ...]:
        snapshot = self._load_snapshot()
        return tuple(
            self._view(snapshot, transaction)
            for transaction in snapshot.transactions
        )

    def get_transaction(self, transaction_id: str) -> TransactionView:
        snapshot = self._load_snapshot()
        try:
            transaction = snapshot.transactions_by_id[transaction_id]
        except KeyError as exc:
            raise ApplicationNotFoundError(
                f"Transaction {transaction_id!r} does not exist"
            ) from exc
        return self._view(snapshot, transaction)

    def update_enrichment(
        self,
        transaction_id: str,
        *,
        merchant: str | None | object = _UNSET,
        category: str | None | object = _UNSET,
        note: str | None | object = _UNSET,
    ) -> TransactionView:
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
            self.paths.financial_summary,
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
                    financial_output_path=self.paths.financial_summary,
                )
                self.runtime.refresh()
                unit_of_work.commit()
        except FileUnitOfWorkRollbackError as exc:
            raise ApplicationStateError(str(exc)) from exc
        except BackendStateError as exc:
            raise ApplicationStateError(str(exc)) from exc

        refreshed = self._load_snapshot()
        return self._view(refreshed, refreshed.transactions_by_id[transaction_id])

    def _write_rules_and_run_due(
        self,
        rules: tuple[ScheduledInputRule, ...],
    ) -> ScheduledInputRunResult:
        assert self.paths.scheduled_input_rules is not None
        try:
            with FileUnitOfWork(
                (self.paths.scheduled_input_rules,),
                label="Scheduled Input rule mutation",
            ) as unit_of_work:
                write_scheduled_input_rules(
                    rules,
                    self.paths.scheduled_input_rules,
                )
                result = self.run_due_scheduled_inputs()
                unit_of_work.commit()
                return result
        except FileUnitOfWorkRollbackError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def _get_scheduled_input_rule(self, rule_id: str) -> ScheduledInputRule:
        rule = next(
            (item for item in self.list_scheduled_inputs() if item.id == rule_id),
            None,
        )
        if rule is None:
            raise ApplicationStateError(
                f"Scheduled Input rule {rule_id!r} disappeared after mutation"
            )
        return rule

    def _plan_mapping_review(
        self,
        snapshot: CurrentHouseholdSnapshot,
        *,
        description: object,
        merchant: object,
        category: object,
    ) -> MappingReviewPlan:
        try:
            return plan_mapping_review(
                transactions=snapshot.transactions,
                source_records_by_transaction_id=(
                    snapshot.source_records_by_transaction_id
                ),
                enrichment_states=snapshot.enrichment_states,
                mappings=snapshot.mappings,
                description=self._required_text(description, "description"),
                merchant=self._required_text(merchant, "merchant"),
                category=self._required_text(category, "category"),
            )
        except MappingReviewError as exc:
            raise ApplicationValidationError(str(exc)) from exc

    @staticmethod
    def _materialize_enrichments(
        snapshot: CurrentHouseholdSnapshot,
        states: tuple[TransactionEnrichmentState, ...],
    ) -> Mapping[str, TransactionEnrichment]:
        states_by_id = {state.transaction_id: state for state in states}
        enrichments = tuple(
            materialize_enrichment_state(
                states_by_id[transaction.id],
                snapshot.source_records_by_transaction_id[transaction.id],
            )
            for transaction in snapshot.transactions
        )
        return MappingProxyType(
            {item.transaction_id: item for item in enrichments}
        )

    @staticmethod
    def _view(
        snapshot: CurrentHouseholdSnapshot,
        transaction: Transaction,
    ) -> TransactionView:
        return TransactionView(
            transaction=transaction,
            source_record=snapshot.source_records_by_transaction_id[transaction.id],
            enrichment=snapshot.enrichments_by_transaction_id[transaction.id],
        )

    def _manual_source_values(
        self,
        *,
        transaction_type: object,
        transaction_date: object,
        amount: object,
        description: object,
    ) -> tuple[str, date, Decimal, str]:
        type_value = self._required_text(transaction_type, "type")
        if type_value not in {"income", "expense"}:
            raise ApplicationValidationError(
                "type must be either 'income' or 'expense'"
            )
        date_value = self._required_text(transaction_date, "date")
        try:
            parsed_date = date.fromisoformat(date_value)
        except ValueError as exc:
            raise ApplicationValidationError(
                f"date must use YYYY-MM-DD format, got {date_value!r}"
            ) from exc
        amount_value = self._required_text(amount, "amount")
        try:
            parsed_amount = Decimal(amount_value)
        except (InvalidOperation, ValueError) as exc:
            raise ApplicationValidationError(
                f"amount must be a decimal string, got {amount_value!r}"
            ) from exc
        if not parsed_amount.is_finite():
            raise ApplicationValidationError(
                f"amount must be a finite decimal string, got {amount_value!r}"
            )
        return (
            type_value,
            parsed_date,
            parsed_amount,
            self._required_text(description, "description"),
        )

    def _scheduled_rule_values(
        self,
        *,
        transaction_type: object,
        amount: object,
        description: object,
    ) -> tuple[str, Decimal, str]:
        type_value = self._required_text(transaction_type, "type")
        if type_value not in {"income", "expense"}:
            raise ApplicationValidationError(
                "type must be either 'income' or 'expense'"
            )
        amount_value = self._required_text(amount, "amount")
        try:
            parsed_amount = Decimal(amount_value)
        except (InvalidOperation, ValueError) as exc:
            raise ApplicationValidationError(
                f"amount must be a decimal string, got {amount_value!r}"
            ) from exc
        if not parsed_amount.is_finite():
            raise ApplicationValidationError(
                f"amount must be a finite decimal string, got {amount_value!r}"
            )
        return (
            type_value,
            parsed_amount,
            self._required_text(description, "description"),
        )

    def _scheduled_date(self, value: object) -> date:
        text = self._required_text(value, "next_date")
        try:
            parsed = date.fromisoformat(text)
        except ValueError as exc:
            raise ApplicationValidationError(
                f"next_date must use YYYY-MM-DD format, got {text!r}"
            ) from exc
        if parsed.day > 28:
            raise ApplicationValidationError(
                "Scheduled Input V1 only supports monthly occurrence days 1-28"
            )
        return parsed

    def _feedback_context(self, value: object) -> FeedbackContext:
        if value is None:
            return FeedbackContext()
        if not isinstance(value, dict):
            raise ApplicationValidationError(
                "Feedback context must be a JSON object or null"
            )
        allowed = {"runtime", "page", "workspace", "entity_type", "entity_id"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ApplicationValidationError(
                f"Unknown Feedback context fields: {unknown!r}"
            )
        runtime = self._optional_text(value.get("runtime"), "context.runtime")
        if runtime is not None and runtime not in FEEDBACK_RUNTIMES:
            raise ApplicationValidationError(
                f"Feedback runtime must be one of {sorted(FEEDBACK_RUNTIMES)!r}"
            )
        entity_type = self._optional_text(
            value.get("entity_type"), "context.entity_type"
        )
        entity_id = self._optional_text(value.get("entity_id"), "context.entity_id")
        if (entity_type is None) != (entity_id is None):
            raise ApplicationValidationError(
                "Feedback context entity_type and entity_id must be provided together"
            )
        return FeedbackContext(
            runtime=runtime,
            page=self._optional_text(value.get("page"), "context.page"),
            workspace=self._optional_text(
                value.get("workspace"), "context.workspace"
            ),
            entity_type=entity_type,
            entity_id=entity_id,
        )

    @staticmethod
    def _boolean(value: object, field: str) -> bool:
        if not isinstance(value, bool):
            raise ApplicationValidationError(f"{field} must be a boolean")
        return value

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or value.strip() == "":
            raise ApplicationValidationError(
                f"{field} must be a non-empty string"
            )
        return value.strip()

    @staticmethod
    def _optional_text(value: object, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ApplicationValidationError(
                f"{field} must be a string or null, got {value!r}"
            )
        stripped = value.strip()
        return stripped or None
