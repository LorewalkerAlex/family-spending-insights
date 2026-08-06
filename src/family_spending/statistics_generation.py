from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from family_spending.ingestion.cmb_email_transactions import (
    CmbTransactionCsvError,
    read_transactions_csv,
)
from family_spending.mapping import (
    MappingDataError,
    MappingResolutionError,
    load_merchant_mappings,
)
from family_spending.refund_reconciliation import (
    RefundReconciliationError,
    reconcile_refunds,
)
from family_spending.settings import (
    CATEGORIES_FILE,
    MERCHANTS_FILE,
    SPENDING_STATISTICS_FILE,
    TRANSACTION_CATEGORY_OVERRIDES_FILE,
    TRANSACTIONS_FILE,
)
from family_spending.spending_statistics import (
    SpendingStatisticsError,
    aggregate_spending,
)
from family_spending.statistics_serialization import (
    StatisticsSerializationError,
    serialize_spending_statistics,
    write_spending_statistics_json,
)
from family_spending.transaction_resolution import (
    TransactionResolutionError,
    resolve_transactions,
    validate_transaction_overrides,
)


@dataclass(frozen=True)
class StatisticsGenerationSummary:
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
    output_path: Path


def generate_spending_statistics(
    transactions_path: Path = TRANSACTIONS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    overrides_path: Path = TRANSACTION_CATEGORY_OVERRIDES_FILE,
    output_path: Path = SPENDING_STATISTICS_FILE,
) -> StatisticsGenerationSummary:
    """Rebuild all derived spending statistics from the complete fact data."""
    raw_transactions = read_transactions_csv(transactions_path)
    mappings = load_merchant_mappings(
        merchants_path,
        categories_path,
        overrides_path,
    )
    validate_transaction_overrides(raw_transactions, mappings)

    reconciliation = reconcile_refunds(
        raw_transactions,
        mappings.description_to_merchant,
    )
    resolution = resolve_transactions(
        reconciliation.net_transactions,
        mappings,
    )
    statistics = aggregate_spending(resolution.transactions)
    payload = serialize_spending_statistics(statistics)
    write_spending_statistics_json(payload, output_path)

    return StatisticsGenerationSummary(
        raw_transactions=len(raw_transactions),
        zero_amount_transactions=reconciliation.zero_amount_transactions,
        refund_transactions=reconciliation.refund_transactions,
        same_merchant_refund_matches=(
            reconciliation.same_merchant_refund_matches
        ),
        same_merchant_matched_amount=(
            reconciliation.same_merchant_matched_amount
        ),
        net_consumption_transactions=len(reconciliation.net_transactions),
        fully_refunded_transactions=reconciliation.fully_refunded_transactions,
        partially_refunded_transactions=reconciliation.partially_refunded_transactions,
        unmatched_refund_count=reconciliation.unmatched_refund_count,
        unmatched_refund_amount=reconciliation.unmatched_refund_amount,
        unclassified_net_transactions=len(resolution.unclassified),
        months=len(statistics.months),
        total_net_spending=statistics.total_spending,
        output_path=output_path,
    )


def format_statistics_generation_report(
    summary: StatisticsGenerationSummary,
) -> str:
    return "\n".join(
        (
            f"Raw transactions: {summary.raw_transactions}",
            f"Zero-amount transactions ignored: {summary.zero_amount_transactions}",
            f"Refund transactions: {summary.refund_transactions}",
            (
                "Same-merchant refund matches: "
                f"{summary.same_merchant_refund_matches}"
            ),
            (
                "Same-merchant matched amount: "
                f"{format(summary.same_merchant_matched_amount, 'f')}"
            ),
            f"Net consumption transactions: {summary.net_consumption_transactions}",
            f"Fully refunded transactions: {summary.fully_refunded_transactions}",
            f"Partially refunded transactions: {summary.partially_refunded_transactions}",
            f"Unmatched refunds: {summary.unmatched_refund_count}",
            f"Unmatched refund amount: {format(summary.unmatched_refund_amount, 'f')}",
            f"Unclassified net transactions: {summary.unclassified_net_transactions}",
            f"Months: {summary.months}",
            f"Total net spending: {format(summary.total_net_spending, 'f')}",
            f"Output: {summary.output_path}",
        )
    )


def main() -> None:
    try:
        summary = generate_spending_statistics()
    except (
        CmbTransactionCsvError,
        MappingDataError,
        MappingResolutionError,
        RefundReconciliationError,
        SpendingStatisticsError,
        StatisticsSerializationError,
        TransactionResolutionError,
        OSError,
    ) as exc:
        raise SystemExit(f"Spending statistics generation failed: {exc}") from exc
    print(format_statistics_generation_report(summary))


if __name__ == "__main__":
    main()
