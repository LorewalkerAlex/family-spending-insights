from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from family_spending.ingestion.cmb_email_transactions import (
    CmbTransactionCsvError,
    read_transactions_csv,
)
from family_spending.manual_source import (
    ManualSourceDataError,
    read_manual_source_entries,
)
from family_spending.mapping import MappingDataError, MappingResolutionError, load_merchant_mappings
from family_spending.month_coverage import MonthCoverageError, load_month_coverage
from family_spending.reconciliation import ReconciliationError
from family_spending.refund_reconciliation import RefundReconciliationError, reconcile_refunds
from family_spending.settings import (
    CATEGORIES_FILE,
    EMAILS_DIR,
    MERCHANTS_FILE,
    SPENDING_STATISTICS_FILE,
    TRANSACTION_CATEGORY_OVERRIDES_FILE,
    TRANSACTIONS_FILE,
)
from family_spending.source_link_store import (
    SourceLinkStoreError,
    read_transaction_source_links,
    write_transaction_source_links,
)
from family_spending.spending_statistics import SpendingStatisticsError, aggregate_spending
from family_spending.statistics_serialization import (
    StatisticsSerializationError,
    serialize_spending_statistics,
    write_spending_statistics_json,
)
from family_spending.transaction_resolution import (
    TransactionResolutionError,
    build_household_domain_state,
)
from family_spending.transactions import TransactionDataError


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
    shown_months: int
    shown_net_spending: Decimal
    output_path: Path


def _default_sibling(path: Path, filename: str) -> Path:
    """Keep test and alternate data roots isolated by deriving new local state beside the selected CMB CSV."""
    return path.parent / filename


def generate_spending_statistics(
    transactions_path: Path = TRANSACTIONS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    overrides_path: Path = TRANSACTION_CATEGORY_OVERRIDES_FILE,
    output_path: Path = SPENDING_STATISTICS_FILE,
    emails_dir: Path = EMAILS_DIR,
    manual_source_path: Path | None = None,
    source_links_path: Path | None = None,
) -> StatisticsGenerationSummary:
    """Rebuild the downstream spending projection from current CMB, Manual, identity-link, and Enrichment state."""
    if manual_source_path is None:
        manual_source_path = _default_sibling(transactions_path, "manual_source_records.jsonl")
    if source_links_path is None:
        source_links_path = _default_sibling(transactions_path, "transaction_source_links.jsonl")

    raw_transactions = read_transactions_csv(transactions_path)
    manual_entries = read_manual_source_entries(manual_source_path)
    existing_links = read_transaction_source_links(source_links_path)
    mappings = load_merchant_mappings(
        merchants_path,
        categories_path,
        overrides_path,
    )
    state = build_household_domain_state(
        raw_transactions,
        manual_entries,
        mappings,
        existing_links=existing_links,
    )
    refund_result = reconcile_refunds(
        state.reconciliation.transactions,
        state.source_records_by_transaction_id,
        state.enrichments_by_transaction_id,
    )
    statistics = aggregate_spending(
        refund_result.net_consumption,
        state.transactions_by_id,
        state.enrichments_by_transaction_id,
    )
    month_coverage = load_month_coverage(
        tuple(month.month for month in statistics.months),
        emails_dir,
    )
    payload = serialize_spending_statistics(statistics, month_coverage)

    # Persist identity relations only after the whole domain/analytics pipeline has validated.
    # Both writers are atomic individually, so a failed validation never changes either file.
    write_transaction_source_links(state.reconciliation.source_links, source_links_path)
    write_spending_statistics_json(payload, output_path)

    shown_month_names = {
        coverage.month for coverage in month_coverage if coverage.show
    }
    shown_months = tuple(
        month for month in statistics.months if month.month in shown_month_names
    )
    shown_net_spending = sum(
        (month.total_spending for month in shown_months),
        start=Decimal("0"),
    )
    unclassified_net_transactions = sum(
        state.enrichments_by_transaction_id[item.transaction_id].is_unclassified
        for item in refund_result.net_consumption
    )
    return StatisticsGenerationSummary(
        raw_transactions=len(raw_transactions),
        zero_amount_transactions=refund_result.zero_amount_transactions,
        refund_transactions=refund_result.refund_transactions,
        same_merchant_refund_matches=refund_result.same_merchant_refund_matches,
        same_merchant_matched_amount=refund_result.same_merchant_matched_amount,
        net_consumption_transactions=len(refund_result.net_consumption),
        fully_refunded_transactions=refund_result.fully_refunded_transactions,
        partially_refunded_transactions=refund_result.partially_refunded_transactions,
        unmatched_refund_count=refund_result.unmatched_refund_count,
        unmatched_refund_amount=refund_result.unmatched_refund_amount,
        unclassified_net_transactions=unclassified_net_transactions,
        months=len(statistics.months),
        total_net_spending=statistics.total_spending,
        shown_months=len(shown_months),
        shown_net_spending=shown_net_spending,
        output_path=output_path,
    )


def format_statistics_generation_report(
    summary: StatisticsGenerationSummary,
) -> str:
    """Expose the established reconciliation counters so real-data regression stays easy to compare manually."""
    return "\n".join(
        (
            f"Raw transactions: {summary.raw_transactions}",
            f"Zero-amount transactions ignored: {summary.zero_amount_transactions}",
            f"Refund transactions: {summary.refund_transactions}",
            f"Same-merchant refund matches: {summary.same_merchant_refund_matches}",
            f"Same-merchant matched amount: {format(summary.same_merchant_matched_amount, 'f')}",
            f"Net consumption transactions: {summary.net_consumption_transactions}",
            f"Fully refunded transactions: {summary.fully_refunded_transactions}",
            f"Partially refunded transactions: {summary.partially_refunded_transactions}",
            f"Unmatched refunds: {summary.unmatched_refund_count}",
            f"Unmatched refund amount: {format(summary.unmatched_refund_amount, 'f')}",
            f"Unclassified net transactions: {summary.unclassified_net_transactions}",
            f"Months: {summary.months}",
            f"Total net spending: {format(summary.total_net_spending, 'f')}",
            f"Shown months: {summary.shown_months}",
            f"Shown net spending: {format(summary.shown_net_spending, 'f')}",
            f"Output: {summary.output_path}",
        )
    )


def main() -> None:
    """Fail cleanly on known source/domain errors so an operator never mistakes a partial rebuild for success."""
    try:
        summary = generate_spending_statistics()
    except (
        CmbTransactionCsvError,
        ManualSourceDataError,
        SourceLinkStoreError,
        MappingDataError,
        MappingResolutionError,
        ReconciliationError,
        RefundReconciliationError,
        SpendingStatisticsError,
        MonthCoverageError,
        StatisticsSerializationError,
        TransactionDataError,
        TransactionResolutionError,
        OSError,
    ) as exc:
        raise SystemExit(f"Spending statistics generation failed: {exc}") from exc
    print(format_statistics_generation_report(summary))


if __name__ == "__main__":
    main()
