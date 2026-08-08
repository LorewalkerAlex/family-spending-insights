from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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
from family_spending.manual_source import (
    MANUAL_SOURCE_RECORDS_FILE,
    ManualSourceAdapter,
    read_manual_source_entries,
)
from family_spending.mapping import MerchantMappings, load_merchant_mappings
from family_spending.settings import (
    CATEGORIES_FILE,
    EMAILS_DIR,
    MERCHANTS_FILE,
    SPENDING_STATISTICS_FILE,
    TRANSACTION_CATEGORY_OVERRIDES_FILE,
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
    """Raised when a client command cannot be represented by the current Enrichment model."""


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
    overrides: Path = TRANSACTION_CATEGORY_OVERRIDES_FILE
    spending_statistics: Path = SPENDING_STATISTICS_FILE
    emails: Path = EMAILS_DIR


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
class _ApplicationSnapshot:
    transactions: tuple[Transaction, ...]
    transactions_by_id: Mapping[str, Transaction]
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]]
    enrichment_states: tuple[TransactionEnrichmentState, ...]
    enrichment_states_by_transaction_id: Mapping[str, TransactionEnrichmentState]
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment]
    mappings: MerchantMappings


class FamilySpendingApplication:
    def __init__(self, paths: ApplicationPaths | None = None) -> None:
        self.paths = paths or ApplicationPaths()

    def initialize(self) -> None:
        """Explicitly synchronize source-driven state once before serving client-only read/edit use cases."""
        generate_spending_statistics(
            transactions_path=self.paths.transactions,
            merchants_path=self.paths.merchants,
            categories_path=self.paths.categories,
            overrides_path=self.paths.overrides,
            output_path=self.paths.spending_statistics,
            emails_dir=self.paths.emails,
            manual_source_path=self.paths.manual_source,
            source_links_path=self.paths.source_links,
            enrichment_state_path=self.paths.enrichment_state,
        )

    def list_categories(self) -> tuple[str, ...]:
        """Return formal categories only; runtime `待分类` remains a state rather than a configurable category."""
        mappings = load_merchant_mappings(
            self.paths.merchants,
            self.paths.categories,
            self.paths.overrides,
        )
        return tuple(sorted(mappings.categories))

    def list_transactions(self) -> tuple[TransactionView, ...]:
        snapshot = self._load_snapshot()
        return tuple(self._view(snapshot, transaction) for transaction in snapshot.transactions)

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
        states_by_id = {state.transaction_id: state for state in states}
        enrichments = tuple(
            materialize_enrichment_state(
                states_by_id[item.id],
                snapshot.source_records_by_transaction_id[item.id],
            )
            for item in snapshot.transactions
        )
        enrichments_by_id = MappingProxyType(
            {item.transaction_id: item for item in enrichments}
        )
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
            self.paths.overrides,
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
