from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from family_spending.enrichment import (
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    OTHER_EXPENSE_CATEGORY,
    OTHER_EXPENSE_REVIEW,
    UNCLASSIFIED_CATEGORY,
    CategorySource,
    TransactionEnrichment,
    TransactionEnrichmentState,
    consumption_review_signals,
    enrichment_state_from_result,
    materialize_enrichment_state,
    validate_enrichment_state_categories,
)
from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.ingestion.cmb_source_adapter import CmbSourceAdapter
from family_spending.manual_source import ManualSourceAdapter, ManualSourceEntry
from family_spending.mapping import (
    MappingEnrichmentResolver,
    MappingResolutionError,
    MerchantMappings,
)
from family_spending.reconciliation import (
    CmbReconciler,
    ManualReconciler,
    ReconciliationContext,
    ReconciliationResult,
)
from family_spending.refund_reconciliation import NetConsumption, reconcile_refunds
from family_spending.source_records import SourceRecord
from family_spending.transactions import (
    Transaction,
    TransactionSourceLink,
    index_authoritative_source_records,
    index_source_records,
    index_transactions,
    rebuild_transactions_from_source_links,
)

CATEGORY_SOURCES: tuple[CategorySource, ...] = (
    "merchant_default",
    "transaction_override",
    "manual_override",
    "unclassified",
)
REVIEW_SIGNALS = (
    OTHER_EXPENSE_REVIEW,
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
)


class TransactionResolutionError(RuntimeError):
    """Raised when Source, Transaction, and Enrichment state is internally inconsistent."""


@dataclass(frozen=True)
class CmbDomainState:
    source_records: tuple[SourceRecord[Any], ...]
    reconciliation: ReconciliationResult
    transactions_by_id: Mapping[str, Transaction]
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]]
    enrichments: tuple[TransactionEnrichment, ...]
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment]
    enrichment_states: tuple[TransactionEnrichmentState, ...]
    enrichment_states_by_transaction_id: Mapping[str, TransactionEnrichmentState]


@dataclass(frozen=True)
class HouseholdDomainState:
    source_records: tuple[SourceRecord[Any], ...]
    reconciliation: ReconciliationResult
    transactions_by_id: Mapping[str, Transaction]
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]]
    enrichments: tuple[TransactionEnrichment, ...]
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment]
    enrichment_states: tuple[TransactionEnrichmentState, ...]
    enrichment_states_by_transaction_id: Mapping[str, TransactionEnrichmentState]


@dataclass(frozen=True)
class TransactionResolutionItem:
    transaction: Transaction
    source_record: SourceRecord[Any]
    enrichment: TransactionEnrichment
    review_signals: tuple[str, ...]


@dataclass(frozen=True)
class TransactionResolutionBatch:
    transactions: tuple[TransactionResolutionItem, ...]
    unclassified: tuple[TransactionResolutionItem, ...]
    reviews_by_signal: Mapping[str, tuple[TransactionResolutionItem, ...]]
    category_source_counts: Mapping[CategorySource, int]
    net_consumption: tuple[NetConsumption, ...]


@dataclass(frozen=True)
class _ManualEnrichmentInput:
    merchant_name: str | None = None
    category: str | None = None
    note: str | None = None


def _retain_current_link_groups(
    links: tuple[TransactionSourceLink, ...],
    source_records: tuple[SourceRecord[Any], ...],
) -> tuple[TransactionSourceLink, ...]:
    """Drop stale Transaction relations as units when their authoritative Source disappears."""
    current_source_ids = {record.id for record in source_records}
    links_by_transaction: dict[str, list[TransactionSourceLink]] = {}
    for link in links:
        links_by_transaction.setdefault(link.transaction_id, []).append(link)
    valid_transactions = {
        transaction_id
        for transaction_id, transaction_links in links_by_transaction.items()
        if any(
            link.role == "authoritative" and link.source_record_id in current_source_ids
            for link in transaction_links
        )
    }
    return tuple(
        link
        for link in links
        if link.transaction_id in valid_transactions
        and link.source_record_id in current_source_ids
    )


def _manual_enrichment_inputs(
    entries: tuple[ManualSourceEntry, ...],
    links: tuple[TransactionSourceLink, ...],
) -> Mapping[str, _ManualEnrichmentInput]:
    """Fold legacy optional Manual enrichment evidence while preserving current source data."""
    transaction_by_source = {
        link.source_record_id: link.transaction_id for link in links
    }
    values: dict[str, _ManualEnrichmentInput] = {}
    for entry in entries:
        transaction_id = transaction_by_source.get(entry.id)
        if transaction_id is None:
            continue
        previous = values.get(transaction_id, _ManualEnrichmentInput())
        values[transaction_id] = _ManualEnrichmentInput(
            merchant_name=(
                entry.merchant_name
                if entry.merchant_name is not None
                else previous.merchant_name
            ),
            category=(
                entry.category if entry.category is not None else previous.category
            ),
            note=entry.note if entry.note is not None else previous.note,
        )
    return MappingProxyType(values)


def _merchant_hints(
    transactions: tuple[Transaction, ...],
    source_records: tuple[SourceRecord[Any], ...],
    links: tuple[TransactionSourceLink, ...],
    manual_entries: tuple[ManualSourceEntry, ...],
    mappings: MerchantMappings,
    current_enrichment_states: Mapping[str, TransactionEnrichmentState],
) -> Mapping[str, str | None]:
    """Expose current Merchant only as Reconciliation evidence; Category never identifies a Transaction."""
    authoritative = index_authoritative_source_records(source_records, links)
    manual_values = _manual_enrichment_inputs(manual_entries, links)
    hints: dict[str, str | None] = {}
    for transaction in transactions:
        persisted = current_enrichment_states.get(transaction.id)
        if persisted is not None:
            hints[transaction.id] = persisted.merchant_name
            continue
        manual_value = manual_values.get(transaction.id)
        if manual_value is not None and manual_value.merchant_name is not None:
            hints[transaction.id] = manual_value.merchant_name
            continue
        source_record = authoritative[transaction.id]
        hints[transaction.id] = (
            mappings.description_to_merchant.get(source_record.description)
            if source_record.description is not None
            else None
        )
    return MappingProxyType(hints)


def _record_merchant_hints(
    source_records: tuple[SourceRecord[Any], ...],
    manual_entries: tuple[ManualSourceEntry, ...],
    mappings: MerchantMappings,
) -> Mapping[str, str | None]:
    """Resolve only the Merchant signal used by candidate matching; Category remains outside identity."""
    manual_by_id = {entry.id: entry for entry in manual_entries}
    hints: dict[str, str | None] = {}
    for record in source_records:
        manual = manual_by_id.get(record.id)
        if manual is not None:
            hints[record.id] = manual.merchant_name
        elif record.description is not None:
            hints[record.id] = mappings.description_to_merchant.get(record.description)
        else:
            hints[record.id] = None
    return MappingProxyType(hints)


def _apply_manual_enrichment(
    base: TransactionEnrichment,
    manual: _ManualEnrichmentInput | None,
    mappings: MerchantMappings,
) -> TransactionEnrichment:
    """Overlay persisted legacy Manual enrichment evidence without changing Transaction Core."""
    if manual is None:
        return base
    merchant_name = (
        manual.merchant_name
        if manual.merchant_name is not None
        else base.merchant_name
    )
    display_name = merchant_name or base.display_name
    default_category = base.default_category
    category = base.category
    category_source = base.category_source
    review_signals = base.review_signals
    if manual.merchant_name is not None:
        new_default = mappings.merchant_to_category.get(manual.merchant_name)
        default_category = new_default
        if base.category_source != "transaction_override" and manual.category is None:
            if new_default is None:
                category = UNCLASSIFIED_CATEGORY
                category_source = "unclassified"
                review_signals = ()
            else:
                category = new_default
                category_source = "merchant_default"
                review_signals = (
                    (OTHER_EXPENSE_REVIEW,)
                    if new_default == OTHER_EXPENSE_CATEGORY
                    else ()
                )
    if manual.category is not None:
        if manual.category not in mappings.categories:
            raise MappingResolutionError(
                f"Manual category {manual.category!r} is not defined in "
                f"{mappings.categories_path}"
            )
        category = manual.category
        category_source = "manual_override"
        review_signals = ()
    return TransactionEnrichment(
        transaction_id=base.transaction_id,
        merchant_name=merchant_name,
        display_name=display_name,
        default_category=default_category,
        category=category,
        category_source=category_source,
        is_unclassified=category == UNCLASSIFIED_CATEGORY,
        review_signals=review_signals,
        note=manual.note if manual.note is not None else base.note,
    )


def build_household_domain_state(
    raw_cmb_transactions: tuple[CmbTransaction, ...],
    manual_entries: tuple[ManualSourceEntry, ...],
    mappings: MerchantMappings,
    *,
    existing_links: tuple[TransactionSourceLink, ...] = (),
    existing_enrichment_states: Mapping[str, TransactionEnrichmentState] | None = None,
) -> HouseholdDomainState:
    """Run source-aware identity stages while preserving current Enrichment for known Transactions."""
    current_enrichment_states = (
        existing_enrichment_states
        if existing_enrichment_states is not None
        else MappingProxyType({})
    )
    cmb_records = CmbSourceAdapter().adapt_all(raw_cmb_transactions)
    manual_records = ManualSourceAdapter().adapt_all(manual_entries)
    source_records = cmb_records + manual_records
    records_by_id = index_source_records(source_records)
    record_merchants = _record_merchant_hints(
        source_records,
        manual_entries,
        mappings,
    )
    current_links = _retain_current_link_groups(existing_links, source_records)
    existing_transactions = rebuild_transactions_from_source_links(
        source_records,
        current_links,
    )
    existing_merchants = (
        _merchant_hints(
            existing_transactions,
            source_records,
            current_links,
            manual_entries,
            mappings,
            current_enrichment_states,
        )
        if existing_transactions
        else MappingProxyType({})
    )
    cmb_result = CmbReconciler().reconcile(
        cmb_records,
        existing_transactions=existing_transactions,
        existing_links=current_links,
        context=ReconciliationContext(
            source_records_by_id=records_by_id,
            merchant_by_transaction_id=existing_merchants,
            merchant_by_source_record_id=record_merchants,
        ),
    )
    merchants_after_cmb = _merchant_hints(
        cmb_result.transactions,
        source_records,
        cmb_result.source_links,
        manual_entries,
        mappings,
        current_enrichment_states,
    )
    manual_result = ManualReconciler().reconcile(
        manual_records,
        existing_transactions=cmb_result.transactions,
        existing_links=cmb_result.source_links,
        context=ReconciliationContext(
            source_records_by_id=records_by_id,
            merchant_by_transaction_id=merchants_after_cmb,
            merchant_by_source_record_id=record_merchants,
        ),
    )
    reconciliation = ReconciliationResult(
        transactions=manual_result.transactions,
        source_links=manual_result.source_links,
        decisions=cmb_result.decisions + manual_result.decisions,
    )
    transactions_by_id = index_transactions(reconciliation.transactions)
    source_records_by_transaction_id = index_authoritative_source_records(
        source_records,
        reconciliation.source_links,
    )
    resolver = MappingEnrichmentResolver(mappings)
    manual_values = _manual_enrichment_inputs(
        manual_entries,
        reconciliation.source_links,
    )
    enrichment_states_list: list[TransactionEnrichmentState] = []
    enrichments_list: list[TransactionEnrichment] = []
    for transaction in reconciliation.transactions:
        source_record = source_records_by_transaction_id[transaction.id]
        persisted = current_enrichment_states.get(transaction.id)
        if persisted is None:
            base = resolver.resolve(transaction, source_record)
            enrichment = _apply_manual_enrichment(
                base,
                manual_values.get(transaction.id),
                mappings,
            )
            enrichment_state = enrichment_state_from_result(enrichment)
        else:
            try:
                validate_enrichment_state_categories(persisted, mappings.categories)
            except ValueError as exc:
                raise TransactionResolutionError(str(exc)) from exc
            enrichment_state = persisted
            enrichment = materialize_enrichment_state(persisted, source_record)
        enrichment_states_list.append(enrichment_state)
        enrichments_list.append(enrichment)

    enrichment_states = tuple(enrichment_states_list)
    enrichments = tuple(enrichments_list)
    return HouseholdDomainState(
        source_records=source_records,
        reconciliation=reconciliation,
        transactions_by_id=transactions_by_id,
        source_records_by_transaction_id=source_records_by_transaction_id,
        enrichments=enrichments,
        enrichments_by_transaction_id=MappingProxyType(
            {item.transaction_id: item for item in enrichments}
        ),
        enrichment_states=enrichment_states,
        enrichment_states_by_transaction_id=MappingProxyType(
            {item.transaction_id: item for item in enrichment_states}
        ),
    )


def build_cmb_domain_state(
    raw_transactions: tuple[CmbTransaction, ...],
    mappings: MerchantMappings,
) -> CmbDomainState:
    """Build the CMB-only form of the same household Source/Reconciliation path."""
    state = build_household_domain_state(raw_transactions, (), mappings)
    return CmbDomainState(
        source_records=state.source_records,
        reconciliation=state.reconciliation,
        transactions_by_id=state.transactions_by_id,
        source_records_by_transaction_id=state.source_records_by_transaction_id,
        enrichments=state.enrichments,
        enrichments_by_transaction_id=state.enrichments_by_transaction_id,
        enrichment_states=state.enrichment_states,
        enrichment_states_by_transaction_id=state.enrichment_states_by_transaction_id,
    )


def resolve_transactions(
    transactions: tuple[CmbTransaction, ...],
    mappings: MerchantMappings,
) -> TransactionResolutionBatch:
    """Build a pure CMB diagnostic batch without a second file-level orchestration path."""
    state = build_cmb_domain_state(transactions, mappings)
    refund_result = reconcile_refunds(
        state.reconciliation.transactions,
        state.source_records_by_transaction_id,
        state.enrichments_by_transaction_id,
    )
    net_by_transaction_id = {
        item.transaction_id: item for item in refund_result.net_consumption
    }
    items: list[TransactionResolutionItem] = []
    for transaction, enrichment in zip(
        state.reconciliation.transactions,
        state.enrichments,
        strict=True,
    ):
        source_record = state.source_records_by_transaction_id[transaction.id]
        net_item = net_by_transaction_id.get(transaction.id)
        review_signals = enrichment.review_signals
        if net_item is not None:
            review_signals = consumption_review_signals(
                enrichment,
                net_item.spending,
            )
        items.append(
            TransactionResolutionItem(
                transaction=transaction,
                source_record=source_record,
                enrichment=enrichment,
                review_signals=review_signals,
            )
        )
    resolved = tuple(items)
    unclassified = tuple(
        item for item in resolved if item.enrichment.is_unclassified
    )
    source_counts = Counter(
        item.enrichment.category_source for item in resolved
    )
    reviews: dict[str, list[TransactionResolutionItem]] = {
        signal: [] for signal in REVIEW_SIGNALS
    }
    for item in resolved:
        for signal in item.review_signals:
            reviews.setdefault(signal, []).append(item)
    return TransactionResolutionBatch(
        transactions=resolved,
        unclassified=unclassified,
        reviews_by_signal=MappingProxyType(
            {signal: tuple(values) for signal, values in reviews.items()}
        ),
        category_source_counts=MappingProxyType(
            {source: source_counts[source] for source in CATEGORY_SOURCES}
        ),
        net_consumption=refund_result.net_consumption,
    )
