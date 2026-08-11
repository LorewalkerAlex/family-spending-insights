from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType
from typing import Any

from family_spending.enrichment import (
    TransactionEnrichment,
    TransactionEnrichmentState,
    materialize_enrichment_state,
    update_category_enrichment_state,
    update_merchant_enrichment_state,
    update_note_enrichment_state,
    validate_enrichment_state_categories,
)
from family_spending.enrichment_store import (
    ENRICHMENT_STATE_FILE,
    read_enrichment_states,
    write_enrichment_states,
)
from family_spending.ingestion.cmb_email_transactions import read_transactions_csv
from family_spending.ingestion.cmb_source_adapter import CmbSourceAdapter
from family_spending.manual_input import (
    delete_manual_input as delete_manual_input_command,
    replace_manual_input as replace_manual_input_command,
    submit_manual_input,
)
from family_spending.manual_source import (
    MANUAL_SOURCE_RECORDS_FILE,
    ManualSourceAdapter,
    ManualSourceDataError,
    ManualSourceEntry,
    create_manual_source_entry,
    read_manual_source_entries,
)
from family_spending.mapping import MerchantMappings, load_merchant_mappings
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
    SCHEDULED_INPUT_RULES_FILE,
    ScheduledInputError,
    ScheduledInputRule,
    ScheduledInputRunResult,
    create_scheduled_input_rule as build_scheduled_input_rule,
    read_scheduled_input_rules,
    run_due_scheduled_inputs as run_due_scheduled_input_commands,
    update_scheduled_input_rule as replace_scheduled_input_rule,
    write_scheduled_input_rules,
)
from family_spending.settings import (
    CATEGORIES_FILE,
    EMAILS_DIR,
    MERCHANTS_FILE,
    SPENDING_STATISTICS_FILE,
    TRANSACTIONS_FILE,
)
from family_spending.source_link_store import (
    TRANSACTION_SOURCE_LINKS_FILE,
    read_transaction_source_links,
)
from family_spending.source_records import SourceRecord
from family_spending.spending_projection import (
    build_spending_projection,
    write_spending_projection,
)
from family_spending.statistics_generation import generate_spending_statistics
from family_spending.transactions import (
    Transaction,
    TransactionDataError,
    index_authoritative_source_records,
    index_transactions,
    rebuild_transactions_from_source_links,
)

_UNSET = object()


class ApplicationError(RuntimeError):
    """Base error for local Application/API use cases."""


class ApplicationNotFoundError(ApplicationError):
    """Raised when a requested current Transaction does not exist."""


class ApplicationValidationError(ApplicationError):
    """Raised when a client command cannot be represented by the current domain model."""


class ApplicationConflictError(ApplicationError):
    """Raised when a valid command cannot be applied uniquely to current state."""


class ApplicationStateError(ApplicationError):
    """Raised when local persisted state has not been synchronized with the current source snapshot."""


@dataclass(frozen=True)
class ApplicationPaths:
    transactions: Path = TRANSACTIONS_FILE
    manual_source: Path = MANUAL_SOURCE_RECORDS_FILE
    source_links: Path = TRANSACTION_SOURCE_LINKS_FILE
    enrichment_state: Path = ENRICHMENT_STATE_FILE
    merchants: Path = MERCHANTS_FILE
    categories: Path = CATEGORIES_FILE
    spending_statistics: Path = SPENDING_STATISTICS_FILE
    emails: Path = EMAILS_DIR
    scheduled_input_rules: Path | None = None

    def __post_init__(self) -> None:
        """Keep test/custom path sets isolated by deriving schedule storage beside transactions."""
        if self.scheduled_input_rules is None:
            object.__setattr__(
                self,
                "scheduled_input_rules",
                self.transactions.parent / SCHEDULED_INPUT_RULES_FILE.name,
            )


@dataclass(frozen=True)
class TransactionView:
    transaction: Transaction
    source_record: SourceRecord[Any]
    enrichment: TransactionEnrichment

    def to_dict(self) -> dict[str, Any]:
        """Expose JSON-safe current Transaction plus Enrichment without copying source-only fields into core."""
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
        """Expose the source identity and reconciled Transaction returned by one Manual Input command."""
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
        """Expose editable Manual Source facts alongside their current Transaction relation."""
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
        """Make the source-identity replacement explicit to clients."""
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
        """Report whether deleting the Manual Source also removed its now-unbacked Transaction."""
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
        """Return one consistent Mapping Review snapshot so UI options and pending groups cannot drift."""
        return {
            "items": [item.to_dict() for item in self.items],
            "merchants": [merchant.to_dict() for merchant in self.merchants],
            "categories": list(self.categories),
        }


@dataclass(frozen=True)
class _ApplicationSnapshot:
    transactions: tuple[Transaction, ...]
    transactions_by_id: Mapping[str, Transaction]
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]]
    enrichment_states: tuple[TransactionEnrichmentState, ...]
    enrichment_states_by_transaction_id: Mapping[str, TransactionEnrichmentState]
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment]
    mappings: MerchantMappings


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    existed: bool
    contents: bytes | None


class FamilySpendingApplication:
    def __init__(self, paths: ApplicationPaths | None = None) -> None:
        self.paths = paths or ApplicationPaths()
        assert self.paths.scheduled_input_rules is not None
        self._scheduled_rules_path = self.paths.scheduled_input_rules

    def initialize(self) -> None:
        """Synchronize Source state, then materialize every scheduled occurrence due today."""
        generate_spending_statistics(
            transactions_path=self.paths.transactions,
            merchants_path=self.paths.merchants,
            categories_path=self.paths.categories,
            output_path=self.paths.spending_statistics,
            emails_dir=self.paths.emails,
            manual_source_path=self.paths.manual_source,
            source_links_path=self.paths.source_links,
            enrichment_state_path=self.paths.enrichment_state,
        )
        self.run_due_scheduled_inputs()

    def list_categories(self) -> tuple[str, ...]:
        """Return formal categories only; runtime `待分类` remains a state rather than a configurable category."""
        mappings = load_merchant_mappings(
            self.paths.merchants,
            self.paths.categories,
        )
        return tuple(sorted(mappings.categories))

    def list_manual_descriptions(self) -> tuple[str, ...]:
        """Return distinct source-native Manual descriptions, newest first, for lightweight reuse hints."""
        seen: set[str] = set()
        descriptions: list[str] = []
        for entry in reversed(read_manual_source_entries(self.paths.manual_source)):
            if entry.description is None or entry.description in seen:
                continue
            seen.add(entry.description)
            descriptions.append(entry.description)
        return tuple(descriptions)

    def list_manual_inputs(self) -> tuple[ManualInputRecordView, ...]:
        """Return current Manual Source facts newest first without flattening them into Transaction Core."""
        snapshot = self._load_snapshot()
        links_by_source = {
            link.source_record_id: link
            for link in read_transaction_source_links(self.paths.source_links)
        }
        views: list[ManualInputRecordView] = []
        for entry in reversed(read_manual_source_entries(self.paths.manual_source)):
            link = links_by_source.get(entry.id)
            if link is None:
                raise ApplicationStateError(
                    f"Manual source record {entry.id!r} has no current Transaction link; run application.initialize()"
                )
            transaction = snapshot.transactions_by_id.get(link.transaction_id)
            if transaction is None:
                raise ApplicationStateError(
                    f"Manual source record {entry.id!r} links to missing Transaction {link.transaction_id!r}"
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

    def list_scheduled_inputs(self) -> tuple[ScheduledInputRule, ...]:
        """Return configured monthly orchestration rules in persisted order."""
        try:
            return read_scheduled_input_rules(self._scheduled_rules_path)
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
        """Create a monthly rule and immediately execute occurrences already due today."""
        type_value, parsed_amount, description_value = self._scheduled_rule_values(
            transaction_type=transaction_type,
            amount=amount,
            description=description,
        )
        next_date_value = self._scheduled_date(next_date)
        enabled_value = self._boolean(enabled, "enabled")
        note_value = self._optional_text(note, "note")
        try:
            rule = build_scheduled_input_rule(
                transaction_type=type_value,
                amount=parsed_amount,
                description=description_value,
                next_date=next_date_value,
                note=note_value,
                enabled=enabled_value,
            )
        except ScheduledInputError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        rules = self.list_scheduled_inputs() + (rule,)
        self._write_rules_and_run_due(rules)
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
        """Replace future rule configuration without rewriting historical Manual occurrences."""
        current_rules = self.list_scheduled_inputs()
        current = next((rule for rule in current_rules if rule.id == rule_id), None)
        if current is None:
            raise ApplicationNotFoundError(
                f"Scheduled Input rule {rule_id!r} does not exist"
            )
        type_value, parsed_amount, description_value = self._scheduled_rule_values(
            transaction_type=transaction_type,
            amount=amount,
            description=description,
        )
        next_date_value = self._scheduled_date(next_date)
        enabled_value = self._boolean(enabled, "enabled")
        note_value = self._optional_text(note, "note")
        try:
            updated = replace_scheduled_input_rule(
                current,
                transaction_type=type_value,
                amount=parsed_amount,
                description=description_value,
                next_date=next_date_value,
                note=note_value,
                enabled=enabled_value,
            )
        except ScheduledInputError as exc:
            raise ApplicationValidationError(str(exc)) from exc
        candidate = tuple(updated if rule.id == rule_id else rule for rule in current_rules)
        self._write_rules_and_run_due(candidate)
        return self._get_scheduled_input_rule(rule_id)

    def delete_scheduled_input(self, rule_id: str) -> ScheduledInputRule:
        """Delete only the future orchestration rule; generated Manual Source history remains."""
        rules = self.list_scheduled_inputs()
        current = next((rule for rule in rules if rule.id == rule_id), None)
        if current is None:
            raise ApplicationNotFoundError(
                f"Scheduled Input rule {rule_id!r} does not exist"
            )
        try:
            write_scheduled_input_rules(
                tuple(rule for rule in rules if rule.id != rule_id),
                self._scheduled_rules_path,
            )
        except ScheduledInputError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return current

    def run_due_scheduled_inputs(
        self,
        as_of: date | None = None,
    ) -> ScheduledInputRunResult:
        """Run every enabled occurrence due through `as_of`, defaulting to the local calendar day."""
        try:
            return run_due_scheduled_input_commands(
                as_of=as_of or date.today(),
                rules_path=self._scheduled_rules_path,
                transactions_path=self.paths.transactions,
                manual_source_path=self.paths.manual_source,
                source_links_path=self.paths.source_links,
                merchants_path=self.paths.merchants,
                categories_path=self.paths.categories,
                output_path=self.paths.spending_statistics,
                emails_dir=self.paths.emails,
                enrichment_state_path=self.paths.enrichment_state,
            )
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except ScheduledInputError as exc:
            raise ApplicationStateError(str(exc)) from exc

    def get_mapping_review_workspace(self) -> MappingReviewWorkspaceView:
        """Aggregate unmapped CMB and Manual descriptions from the current reconciled snapshot."""
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
        """Preview the exact Mapping and Enrichment propagation before any authoritative file changes."""
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
        """Commit reviewed Mapping plus affected Enrichment state as one rollback-protected mutation."""
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
        )
        snapshots = tuple(_snapshot_file(path) for path in persisted_paths)
        try:
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
                validate_enrichment_state_categories(state, refreshed_mappings.categories)
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
            write_enrichment_states(plan.enrichment_states, self.paths.enrichment_state)
            write_spending_projection(projection, self.paths.spending_statistics)
        except MappingReviewError as exc:
            self._restore_mapping_review_files(snapshots, exc)
            raise ApplicationValidationError(str(exc)) from exc
        except Exception as exc:
            self._restore_mapping_review_files(snapshots, exc)
            raise
        return plan.preview

    def list_transactions(self) -> tuple[TransactionView, ...]:
        """Return the current joined Transaction views in persisted Transaction order."""
        snapshot = self._load_snapshot()
        return tuple(self._view(snapshot, transaction) for transaction in snapshot.transactions)

    def get_transaction(self, transaction_id: str) -> TransactionView:
        """Return one joined current Transaction view or fail with an Application-level not-found error."""
        snapshot = self._load_snapshot()
        try:
            transaction = snapshot.transactions_by_id[transaction_id]
        except KeyError as exc:
            raise ApplicationNotFoundError(
                f"Transaction {transaction_id!r} does not exist"
            ) from exc
        return self._view(snapshot, transaction)

    def create_manual_input(
        self,
        *,
        transaction_type: object,
        transaction_date: object,
        amount: object,
        description: object,
        note: object = None,
    ) -> ManualInputView:
        """Persist one source-native Manual description and run the shared Mapping/Reconciliation pipeline."""
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
            result = submit_manual_input(
                entry,
                transactions_path=self.paths.transactions,
                manual_source_path=self.paths.manual_source,
                source_links_path=self.paths.source_links,
                merchants_path=self.paths.merchants,
                categories_path=self.paths.categories,
                output_path=self.paths.spending_statistics,
                emails_dir=self.paths.emails,
                enrichment_state_path=self.paths.enrichment_state,
            )
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
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
        """Replace one Manual Source record with a new identity instead of mutating Transaction Core in place."""
        current = next(
            (
                entry
                for entry in read_manual_source_entries(self.paths.manual_source)
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
            result = replace_manual_input_command(
                source_record_id,
                replacement,
                transactions_path=self.paths.transactions,
                manual_source_path=self.paths.manual_source,
                source_links_path=self.paths.source_links,
                merchants_path=self.paths.merchants,
                categories_path=self.paths.categories,
                output_path=self.paths.spending_statistics,
                emails_dir=self.paths.emails,
                enrichment_state_path=self.paths.enrichment_state,
                update_note=update_note,
            )
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except ManualSourceDataError as exc:
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
        """Delete one Manual Source record while preserving any Transaction still backed by another source."""
        if not any(
            entry.id == source_record_id
            for entry in read_manual_source_entries(self.paths.manual_source)
        ):
            raise ApplicationNotFoundError(
                f"Manual source record {source_record_id!r} does not exist"
            )
        try:
            result = delete_manual_input_command(
                source_record_id,
                transactions_path=self.paths.transactions,
                manual_source_path=self.paths.manual_source,
                source_links_path=self.paths.source_links,
                merchants_path=self.paths.merchants,
                categories_path=self.paths.categories,
                output_path=self.paths.spending_statistics,
                emails_dir=self.paths.emails,
                enrichment_state_path=self.paths.enrichment_state,
            )
        except ReconciliationError as exc:
            raise ApplicationConflictError(str(exc)) from exc
        except ManualSourceDataError as exc:
            raise ApplicationStateError(str(exc)) from exc
        return ManualInputDeletionView(
            source_record_id=result.source_record_id,
            transaction_id=result.transaction_id,
            transaction_removed=result.transaction_removed,
        )

    def update_enrichment(
        self,
        transaction_id: str,
        *,
        merchant: str | None | object = _UNSET,
        category: str | None | object = _UNSET,
        note: str | None | object = _UNSET,
    ) -> TransactionView:
        """Apply one Enrichment command and rebuild only refund/analytics/projection downstream stages."""
        if merchant is _UNSET and category is _UNSET and note is _UNSET:
            raise ApplicationValidationError(
                "Enrichment update requires at least one of merchant, category, or note"
            )
        snapshot = self._load_snapshot()
        try:
            transaction = snapshot.transactions_by_id[transaction_id]
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
            if category_name is not None and category_name not in snapshot.mappings.categories:
                raise ApplicationValidationError(
                    f"Unknown category {category_name!r}; use one of the formal configured categories"
                )
            updated = update_category_enrichment_state(updated, category_name)
        if note is not _UNSET:
            updated = update_note_enrichment_state(
                updated,
                self._optional_text(note, "note"),
            )
        try:
            validate_enrichment_state_categories(updated, snapshot.mappings.categories)
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
        previous_projection = build_spending_projection(
            snapshot.transactions,
            snapshot.transactions_by_id,
            snapshot.source_records_by_transaction_id,
            snapshot.enrichments_by_transaction_id,
            self.paths.emails,
        )
        # Projection is derived and can be rebuilt, so write it before the authoritative
        # Enrichment state. If the authoritative write fails, restore the previous projection.
        write_spending_projection(projection, self.paths.spending_statistics)
        try:
            write_enrichment_states(states, self.paths.enrichment_state)
        except Exception:
            write_spending_projection(previous_projection, self.paths.spending_statistics)
            raise
        return TransactionView(
            transaction=transaction,
            source_record=snapshot.source_records_by_transaction_id[transaction_id],
            enrichment=enrichments_by_id[transaction_id],
        )

    def _write_rules_and_run_due(
        self,
        rules: tuple[ScheduledInputRule, ...],
    ) -> ScheduledInputRunResult:
        """Commit a rule mutation and any immediately due Manual occurrences as one Application command."""
        rule_snapshot = _snapshot_file(self._scheduled_rules_path)
        try:
            write_scheduled_input_rules(rules, self._scheduled_rules_path)
            return self.run_due_scheduled_inputs()
        except Exception as exc:
            try:
                _restore_file(rule_snapshot)
            except Exception as rollback_error:
                raise ApplicationStateError(
                    "Scheduled Input rule mutation failed and rollback could not restore rule state: "
                    f"original={exc}; rollback={rollback_error}"
                ) from rollback_error
            raise

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
        snapshot: _ApplicationSnapshot,
        *,
        description: object,
        merchant: object,
        category: object,
    ) -> MappingReviewPlan:
        """Normalize client values once before delegating deterministic review planning to the domain service."""
        description_value = self._required_text(description, "description")
        merchant_value = self._required_text(merchant, "merchant")
        category_value = self._required_text(category, "category")
        try:
            return plan_mapping_review(
                transactions=snapshot.transactions,
                source_records_by_transaction_id=snapshot.source_records_by_transaction_id,
                enrichment_states=snapshot.enrichment_states,
                mappings=snapshot.mappings,
                description=description_value,
                merchant=merchant_value,
                category=category_value,
            )
        except MappingReviewError as exc:
            raise ApplicationValidationError(str(exc)) from exc

    def _materialize_enrichments(
        self,
        snapshot: _ApplicationSnapshot,
        states: tuple[TransactionEnrichmentState, ...],
    ) -> Mapping[str, TransactionEnrichment]:
        """Materialize a candidate Enrichment state set against unchanged authoritative Source Records."""
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

    def _restore_mapping_review_files(
        self,
        snapshots: tuple[_FileSnapshot, ...],
        original_error: Exception,
    ) -> None:
        """Restore every persisted participant if Mapping mutation fails after any file has changed."""
        try:
            for snapshot in reversed(snapshots):
                _restore_file(snapshot)
        except Exception as rollback_error:
            raise ApplicationStateError(
                "Mapping Review mutation failed and rollback could not restore all persisted files: "
                f"original={original_error}; rollback={rollback_error}"
            ) from rollback_error

    def _load_snapshot(self) -> _ApplicationSnapshot:
        """Rehydrate already-reconciled current state without invoking either Reconciler."""
        raw_cmb = read_transactions_csv(self.paths.transactions)
        manual_entries = read_manual_source_entries(self.paths.manual_source)
        source_records = (
            CmbSourceAdapter().adapt_all(raw_cmb)
            + ManualSourceAdapter().adapt_all(manual_entries)
        )
        source_links = read_transaction_source_links(self.paths.source_links)
        linked_source_ids = {link.source_record_id for link in source_links}
        unreconciled_source_ids = [
            record.id for record in source_records if record.id not in linked_source_ids
        ]
        if unreconciled_source_ids:
            raise ApplicationStateError(
                "Current Source state contains unreconciled records; run application.initialize() "
                f"after source changes: {unreconciled_source_ids!r}"
            )
        try:
            transactions = rebuild_transactions_from_source_links(source_records, source_links)
            transactions_by_id = index_transactions(transactions)
            authoritative = index_authoritative_source_records(source_records, source_links)
        except TransactionDataError as exc:
            raise ApplicationStateError(
                "Current Source/Transaction link state is stale; run application.initialize() after source changes"
            ) from exc
        states = read_enrichment_states(self.paths.enrichment_state)
        states_by_id = {state.transaction_id: state for state in states}
        mappings = load_merchant_mappings(
            self.paths.merchants,
            self.paths.categories,
        )
        missing = [transaction.id for transaction in transactions if transaction.id not in states_by_id]
        if missing:
            raise ApplicationStateError(
                "Current Enrichment state is missing Transactions; run application.initialize() "
                f"after source changes: {missing!r}"
            )
        current_states = tuple(states_by_id[transaction.id] for transaction in transactions)
        enrichments: list[TransactionEnrichment] = []
        for transaction, state in zip(transactions, current_states, strict=True):
            try:
                validate_enrichment_state_categories(state, mappings.categories)
            except ValueError as exc:
                raise ApplicationStateError(str(exc)) from exc
            enrichments.append(
                materialize_enrichment_state(state, authoritative[transaction.id])
            )
        return _ApplicationSnapshot(
            transactions=transactions,
            transactions_by_id=transactions_by_id,
            source_records_by_transaction_id=authoritative,
            enrichment_states=current_states,
            enrichment_states_by_transaction_id=MappingProxyType(
                {state.transaction_id: state for state in current_states}
            ),
            enrichments_by_transaction_id=MappingProxyType(
                {item.transaction_id: item for item in enrichments}
            ),
            mappings=mappings,
        )

    def _view(
        self,
        snapshot: _ApplicationSnapshot,
        transaction: Transaction,
    ) -> TransactionView:
        """Join the current view at query time while preserving each underlying domain boundary."""
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
        """Normalize the source-native fields shared by Manual create and correction commands."""
        type_value = self._required_text(transaction_type, "type")
        if type_value not in {"income", "expense"}:
            raise ApplicationValidationError("type must be either 'income' or 'expense'")
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
        description_value = self._required_text(description, "description")
        return type_value, parsed_date, parsed_amount, description_value

    def _scheduled_rule_values(
        self,
        *,
        transaction_type: object,
        amount: object,
        description: object,
    ) -> tuple[str, Decimal, str]:
        """Normalize the source-native fields copied into future Manual occurrences."""
        type_value = self._required_text(transaction_type, "type")
        if type_value not in {"income", "expense"}:
            raise ApplicationValidationError("type must be either 'income' or 'expense'")
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
        description_value = self._required_text(description, "description")
        return type_value, parsed_amount, description_value

    def _scheduled_date(self, value: object) -> date:
        """Parse the next monthly occurrence and keep V1 recurrence away from month-end ambiguity."""
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

    @staticmethod
    def _boolean(value: object, field: str) -> bool:
        """Require an explicit JSON boolean instead of accepting truthy client values."""
        if not isinstance(value, bool):
            raise ApplicationValidationError(f"{field} must be a boolean")
        return value

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        """Require one non-empty textual client value and normalize surrounding whitespace."""
        if not isinstance(value, str) or value.strip() == "":
            raise ApplicationValidationError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_text(value: object, field: str) -> str | None:
        """Normalize client text consistently while rejecting JSON values with the wrong type."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ApplicationValidationError(
                f"{field} must be a string or null, got {value!r}"
            )
        stripped = value.strip()
        return stripped or None


def _snapshot_file(path: Path) -> _FileSnapshot:
    """Capture exact bytes so a multi-file mutation can restore the pre-command state."""
    if not path.exists():
        return _FileSnapshot(path=path, existed=False, contents=None)
    return _FileSnapshot(path=path, existed=True, contents=path.read_bytes())


def _restore_file(snapshot: _FileSnapshot) -> None:
    """Restore one captured file atomically, or remove a file that did not exist before the command."""
    if not snapshot.existed:
        snapshot.path.unlink(missing_ok=True)
        return
    assert snapshot.contents is not None
    snapshot.path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=snapshot.path.parent,
        prefix=f".{snapshot.path.name}.",
        suffix=".rollback",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(snapshot.contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, snapshot.path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise
