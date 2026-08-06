from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    CmbTransactionCsvError,
    read_transactions_csv,
)
from family_spending.mapping import (
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    OTHER_EXPENSE_REVIEW,
    CategorySource,
    MappingDataError,
    MappingResolutionError,
    MerchantMappings,
    ResolvedTransaction,
    load_merchant_mappings,
    resolve_transaction,
)
from family_spending.settings import (
    CATEGORIES_FILE,
    MERCHANTS_FILE,
    TRANSACTION_CATEGORY_OVERRIDES_FILE,
    TRANSACTIONS_FILE,
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
    """Raised when complete transaction and Mapping data are inconsistent."""


@dataclass(frozen=True)
class TransactionResolutionBatch:
    transactions: tuple[ResolvedTransaction, ...]
    unclassified: tuple[ResolvedTransaction, ...]
    reviews_by_signal: Mapping[str, tuple[ResolvedTransaction, ...]]
    category_source_counts: Mapping[CategorySource, int]


def resolve_transactions(
    transactions: tuple[CmbTransaction, ...],
    mappings: MerchantMappings,
) -> TransactionResolutionBatch:
    resolved_transactions = tuple(resolve_transaction(transaction, mappings) for transaction in transactions)
    unclassified = tuple(item for item in resolved_transactions if item.is_unmatched)

    category_source_counter = Counter(item.category_source for item in resolved_transactions)
    category_source_counts = MappingProxyType(
        {source: category_source_counter[source] for source in CATEGORY_SOURCES}
    )

    reviews: dict[str, list[ResolvedTransaction]] = {signal: [] for signal in REVIEW_SIGNALS}
    for item in resolved_transactions:
        for signal in item.review_signals:
            reviews.setdefault(signal, []).append(item)
    reviews_by_signal = MappingProxyType(
        {signal: tuple(items) for signal, items in reviews.items()}
    )

    return TransactionResolutionBatch(
        transactions=resolved_transactions,
        unclassified=unclassified,
        reviews_by_signal=reviews_by_signal,
        category_source_counts=category_source_counts,
    )


def _validate_all_overrides_consumed(
    batch: TransactionResolutionBatch,
    mappings: MerchantMappings,
) -> None:
    consumed_override_ids = {
        item.transaction.transaction_id
        for item in batch.transactions
        if item.category_source == "transaction_override"
    }
    missing_override_ids = sorted(
        set(mappings.transaction_category_overrides) - consumed_override_ids
    )
    if missing_override_ids:
        raise TransactionResolutionError(
            f"Official overrides in {mappings.overrides_path} do not match transactions: "
            f"missing transaction_id values {missing_override_ids!r}"
        )


def resolve_transactions_from_files(
    transactions_path: Path = TRANSACTIONS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    overrides_path: Path = TRANSACTION_CATEGORY_OVERRIDES_FILE,
) -> TransactionResolutionBatch:
    transactions = read_transactions_csv(transactions_path)
    mappings = load_merchant_mappings(merchants_path, categories_path, overrides_path)
    batch = resolve_transactions(transactions, mappings)
    _validate_all_overrides_consumed(batch, mappings)
    return batch


def _format_transaction(item: ResolvedTransaction) -> str:
    transaction = item.transaction
    return (
        f"transaction_id={transaction.transaction_id} | "
        f"transaction_date={transaction.transaction_date.isoformat()} | "
        f"amount={format(transaction.amount, 'f')} | "
        f"description={transaction.description} | "
        f"display_name={item.display_name} | "
        f"category={item.category}"
    )


def format_transaction_resolution_report(batch: TransactionResolutionBatch) -> str:
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
    try:
        batch = resolve_transactions_from_files()
    except (
        CmbTransactionCsvError,
        MappingDataError,
        MappingResolutionError,
        TransactionResolutionError,
    ) as exc:
        raise SystemExit(f"Transaction resolution failed: {exc}") from exc
    print(format_transaction_resolution_report(batch))


if __name__ == "__main__":
    main()
