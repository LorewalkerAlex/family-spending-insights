from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from family_spending.enrichment import (
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    OTHER_EXPENSE_REVIEW,
    CategorySource,
    TransactionEnrichment,
    consumption_review_signals,
    resolve_enrichments,
)
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    CmbTransactionCsvError,
    read_transactions_csv,
)
from family_spending.ingestion.cmb_source_adapter import CmbSourceAdapter
from family_spending.mapping import (
    MappingDataError,
    MappingEnrichmentResolver,
    MappingResolutionError,
    MerchantMappings,
    UnboundTransactionOverrideError,
    bind_transaction_category_overrides,
    load_merchant_mappings,
)
from family_spending.reconciliation import (
    CmbReconciler,
    ReconciliationError,
    ReconciliationResult,
)
from family_spending.refund_reconciliation import (
    NetConsumption,
    RefundReconciliationError,
    reconcile_refunds,
)
from family_spending.settings import (
    CATEGORIES_FILE,
    MERCHANTS_FILE,
    TRANSACTION_CATEGORY_OVERRIDES_FILE,
    TRANSACTIONS_FILE,
)
from family_spending.source_records import SourceRecord
from family_spending.transactions import (
    Transaction,
    TransactionDataError,
    index_authoritative_source_records,
    index_transactions,
)

CATEGORY_SOURCES: tuple[CategorySource, ...] = (
    "merchant_default",
    "transaction_override",
    "unclassified",
)
REVIEW_SIGNALS = (
    OTHER_EXPENSE_REVIEW,
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
)


class TransactionResolutionError(RuntimeError):
    """Raised when the complete CMB source, identity, and Enrichment snapshot is internally inconsistent."""


@dataclass(frozen=True)
class CmbDomainState:
    source_records: tuple[SourceRecord[Any], ...]
    reconciliation: ReconciliationResult
    transactions_by_id: Mapping[str, Transaction]
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]]
    enrichments: tuple[TransactionEnrichment, ...]
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment]


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


def build_cmb_domain_state(
    raw_transactions: tuple[CmbTransaction, ...],
    mappings: MerchantMappings,
) -> CmbDomainState:
    """Build one domain snapshot so CLI and statistics share identity and Enrichment semantics."""
    source_records = CmbSourceAdapter().adapt_all(raw_transactions)
    reconciliation = CmbReconciler().reconcile(source_records)
    transactions_by_id = index_transactions(reconciliation.transactions)
    source_records_by_transaction_id = index_authoritative_source_records(
        source_records,
        reconciliation.source_links,
    )
    try:
        bound_overrides = bind_transaction_category_overrides(
            mappings,
            reconciliation.source_links,
        )
    except UnboundTransactionOverrideError as exc:
        # Missing legacy override IDs have historically been a complete-transaction-set
        # validation error, so keep that public behavior while binding through Source Records.
        raise TransactionResolutionError(str(exc)) from exc
    resolver = MappingEnrichmentResolver(mappings, bound_overrides)
    enrichments = resolve_enrichments(
        reconciliation.transactions,
        source_records_by_transaction_id,
        resolver,
    )
    enrichments_by_transaction_id = MappingProxyType(
        {item.transaction_id: item for item in enrichments}
    )
    return CmbDomainState(
        source_records=source_records,
        reconciliation=reconciliation,
        transactions_by_id=transactions_by_id,
        source_records_by_transaction_id=source_records_by_transaction_id,
        enrichments=enrichments,
        enrichments_by_transaction_id=enrichments_by_transaction_id,
    )


def validate_transaction_overrides(
    transactions: tuple[CmbTransaction, ...],
    mappings: MerchantMappings,
) -> None:
    """Resolve every legacy override through Source Record identity so fully refunded purchases remain validated."""
    build_cmb_domain_state(transactions, mappings)


def resolve_transactions(
    transactions: tuple[CmbTransaction, ...],
    mappings: MerchantMappings,
) -> TransactionResolutionBatch:
    """Use net spending only for reviews whose meaning actually depends on net amount."""
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

    resolved_transactions = tuple(items)
    unclassified = tuple(
        item for item in resolved_transactions if item.enrichment.is_unclassified
    )
    category_source_counter = Counter(
        item.enrichment.category_source for item in resolved_transactions
    )
    category_source_counts = MappingProxyType(
        {source: category_source_counter[source] for source in CATEGORY_SOURCES}
    )
    reviews: dict[str, list[TransactionResolutionItem]] = {
        signal: [] for signal in REVIEW_SIGNALS
    }
    for item in resolved_transactions:
        for signal in item.review_signals:
            reviews.setdefault(signal, []).append(item)
    reviews_by_signal = MappingProxyType(
        {signal: tuple(review_items) for signal, review_items in reviews.items()}
    )
    return TransactionResolutionBatch(
        transactions=resolved_transactions,
        unclassified=unclassified,
        reviews_by_signal=reviews_by_signal,
        category_source_counts=category_source_counts,
        net_consumption=refund_result.net_consumption,
    )


def resolve_transactions_from_files(
    transactions_path: Path = TRANSACTIONS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    overrides_path: Path = TRANSACTION_CATEGORY_OVERRIDES_FILE,
) -> TransactionResolutionBatch:
    """Keep the CLI file-based while routing decisions through the shared domain pipeline."""
    transactions = read_transactions_csv(transactions_path)
    mappings = load_merchant_mappings(
        merchants_path,
        categories_path,
        overrides_path,
    )
    return resolve_transactions(transactions, mappings)


def _format_transaction(item: TransactionResolutionItem) -> str:
    """Show both source and system IDs because their separation is the key invariant introduced by this migration."""
    transaction = item.transaction
    source_record = item.source_record
    enrichment = item.enrichment
    return (
        f"transaction_id={transaction.id} | "
        f"source_record_id={source_record.id} | "
        f"transaction_date={transaction.transaction_date.isoformat()} | "
        f"amount={format(transaction.amount, 'f')} | "
        f"description={source_record.description or ''} | "
        f"display_name={enrichment.display_name} | "
        f"category={enrichment.category}"
    )


def format_transaction_resolution_report(
    batch: TransactionResolutionBatch,
) -> str:
    """Keep diagnostics compact so reviewers can inspect decisions without reading raw files."""
    lines = [
        f"Transactions: {len(batch.transactions)}",
        f"Merchant defaults: {batch.category_source_counts['merchant_default']}",
        f"Transaction overrides: {batch.category_source_counts['transaction_override']}",
        f"Unclassified: {batch.category_source_counts['unclassified']}",
        "",
        "Review signals:",
    ]
    for signal, items in batch.reviews_by_signal.items():
        lines.append(f"- {signal}: {len(items)}")
    if batch.unclassified:
        lines.extend(("", "Unclassified transactions:"))
        lines.extend(f"- {_format_transaction(item)}" for item in batch.unclassified)
    for signal, items in batch.reviews_by_signal.items():
        if not items:
            continue
        lines.extend(("", f"Review transactions: {signal}"))
        lines.extend(f"- {_format_transaction(item)}" for item in items)
    return "\n".join(lines)


def main() -> None:
    """Fail the command on any data-contract violation so a successful report always represents a coherent snapshot."""
    try:
        batch = resolve_transactions_from_files()
    except (
        CmbTransactionCsvError,
        MappingDataError,
        MappingResolutionError,
        ReconciliationError,
        RefundReconciliationError,
        TransactionDataError,
        TransactionResolutionError,
    ) as exc:
        raise SystemExit(f"Transaction resolution failed: {exc}") from exc
    print(format_transaction_resolution_report(batch))


if __name__ == "__main__":
    main()
