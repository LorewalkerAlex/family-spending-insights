from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from family_spending.backend.paths import BackendPaths
from family_spending.enrichment import (
    TransactionEnrichment,
    TransactionEnrichmentState,
    materialize_enrichment_state,
    validate_enrichment_state_categories,
)
from family_spending.enrichment_store import read_enrichment_states
from family_spending.ingestion.cmb_email_transactions import read_transactions_csv
from family_spending.ingestion.cmb_source_adapter import CmbSourceAdapter
from family_spending.manual_source import (
    ManualSourceAdapter,
    ManualSourceEntry,
    read_manual_source_entries,
)
from family_spending.mapping import MerchantMappings, load_merchant_mappings
from family_spending.source_link_store import read_transaction_source_links
from family_spending.source_records import SourceRecord
from family_spending.transactions import (
    Transaction,
    TransactionDataError,
    TransactionSourceLink,
    index_authoritative_source_records,
    index_transactions,
    rebuild_transactions_from_source_links,
)


class BackendStateError(RuntimeError):
    """Raised when persisted backend files do not describe one coherent current snapshot."""


@dataclass(frozen=True)
class CurrentHouseholdSnapshot:
    """Joined current state consumed by queries and downstream-only pipeline stages."""

    source_records: tuple[SourceRecord[Any], ...]
    manual_entries: tuple[ManualSourceEntry, ...]
    source_links: tuple[TransactionSourceLink, ...]
    transactions: tuple[Transaction, ...]
    transactions_by_id: Mapping[str, Transaction]
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]]
    enrichment_states: tuple[TransactionEnrichmentState, ...]
    enrichment_states_by_transaction_id: Mapping[str, TransactionEnrichmentState]
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment]
    mappings: MerchantMappings


def load_current_household_snapshot(paths: BackendPaths) -> CurrentHouseholdSnapshot:
    """Rehydrate already-reconciled state without running Source identity decisions again."""
    raw_cmb = read_transactions_csv(paths.transactions)
    manual_entries = read_manual_source_entries(paths.manual_source)
    source_records = (
        CmbSourceAdapter().adapt_all(raw_cmb)
        + ManualSourceAdapter().adapt_all(manual_entries)
    )
    source_links = read_transaction_source_links(paths.source_links)

    linked_source_ids = {link.source_record_id for link in source_links}
    unreconciled_source_ids = [
        record.id for record in source_records if record.id not in linked_source_ids
    ]
    if unreconciled_source_ids:
        raise BackendStateError(
            "Current Source state contains unreconciled records; "
            "run backend sync after source changes: "
            f"{unreconciled_source_ids!r}"
        )

    try:
        transactions = rebuild_transactions_from_source_links(source_records, source_links)
        transactions_by_id = index_transactions(transactions)
        authoritative = index_authoritative_source_records(source_records, source_links)
    except TransactionDataError as exc:
        raise BackendStateError(
            "Current Source/Transaction link state is stale; run backend sync after source changes"
        ) from exc

    states = read_enrichment_states(paths.enrichment_state)
    states_by_id = {state.transaction_id: state for state in states}
    missing = [
        transaction.id
        for transaction in transactions
        if transaction.id not in states_by_id
    ]
    if missing:
        raise BackendStateError(
            "Current Enrichment state is missing Transactions; "
            "run backend sync after source changes: "
            f"{missing!r}"
        )

    mappings = load_merchant_mappings(paths.merchants, paths.categories)
    current_states = tuple(states_by_id[transaction.id] for transaction in transactions)
    enrichments: list[TransactionEnrichment] = []
    for transaction, state in zip(transactions, current_states, strict=True):
        try:
            validate_enrichment_state_categories(state, mappings.categories)
        except ValueError as exc:
            raise BackendStateError(str(exc)) from exc
        enrichments.append(
            materialize_enrichment_state(state, authoritative[transaction.id])
        )

    return CurrentHouseholdSnapshot(
        source_records=source_records,
        manual_entries=manual_entries,
        source_links=source_links,
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
