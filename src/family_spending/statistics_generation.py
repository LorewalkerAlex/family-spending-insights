from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from family_spending.backend.paths import BackendPaths
from family_spending.backend.pipeline import (
    HouseholdPipeline,
    HouseholdPipelineRollbackError,
)
from family_spending.enrichment_store import EnrichmentStateStoreError
from family_spending.financial_projection import FinancialProjectionError
from family_spending.ingestion.cmb_email_transactions import CmbTransactionCsvError
from family_spending.manual_source import ManualSourceDataError
from family_spending.mapping import MappingDataError, MappingResolutionError
from family_spending.month_coverage import MonthCoverageError
from family_spending.reconciliation import ReconciliationError
from family_spending.refund_reconciliation import RefundReconciliationError
from family_spending.settings import (
    CATEGORIES_FILE,
    EMAILS_DIR,
    MERCHANTS_FILE,
    SPENDING_STATISTICS_FILE,
    TRANSACTIONS_FILE,
)
from family_spending.source_link_store import SourceLinkStoreError
from family_spending.spending_statistics import SpendingStatisticsError
from family_spending.statistics_serialization import StatisticsSerializationError
from family_spending.transaction_resolution import TransactionResolutionError
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


class StatisticsGenerationRollbackError(RuntimeError):
    """Compatibility error for callers of the historical statistics-generation entrypoint."""


def generate_spending_statistics(
    transactions_path: Path = TRANSACTIONS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    output_path: Path = SPENDING_STATISTICS_FILE,
    emails_dir: Path = EMAILS_DIR,
    manual_source_path: Path | None = None,
    source_links_path: Path | None = None,
    enrichment_state_path: Path | None = None,
    financial_output_path: Path | None = None,
) -> StatisticsGenerationSummary:
    """Compatibility wrapper for the formal full Source synchronization pipeline."""
    paths = BackendPaths.for_generation(
        transactions=transactions_path,
        merchants=merchants_path,
        categories=categories_path,
        spending_statistics=output_path,
        emails=emails_dir,
        manual_source=manual_source_path,
        source_links=source_links_path,
        enrichment_state=enrichment_state_path,
        financial_summary=financial_output_path,
    )
    try:
        result = HouseholdPipeline(paths).sync_sources()
    except HouseholdPipelineRollbackError as exc:
        raise StatisticsGenerationRollbackError(str(exc)) from exc
    return StatisticsGenerationSummary(
        raw_transactions=result.raw_transactions,
        zero_amount_transactions=result.zero_amount_transactions,
        refund_transactions=result.refund_transactions,
        same_merchant_refund_matches=result.same_merchant_refund_matches,
        same_merchant_matched_amount=result.same_merchant_matched_amount,
        net_consumption_transactions=result.net_consumption_transactions,
        fully_refunded_transactions=result.fully_refunded_transactions,
        partially_refunded_transactions=result.partially_refunded_transactions,
        unmatched_refund_count=result.unmatched_refund_count,
        unmatched_refund_amount=result.unmatched_refund_amount,
        unclassified_net_transactions=result.unclassified_net_transactions,
        months=result.months,
        total_net_spending=result.total_net_spending,
        shown_months=result.shown_months,
        shown_net_spending=result.shown_net_spending,
        output_path=output_path,
    )


def format_statistics_generation_report(
    summary: StatisticsGenerationSummary,
) -> str:
    """Expose established reconciliation counters so real-data regression stays easy to compare."""
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
    """Fail cleanly on known source/domain errors so a successful sync is always coherent."""
    try:
        summary = generate_spending_statistics()
    except (
        CmbTransactionCsvError,
        ManualSourceDataError,
        SourceLinkStoreError,
        EnrichmentStateStoreError,
        MappingDataError,
        MappingResolutionError,
        ReconciliationError,
        RefundReconciliationError,
        SpendingStatisticsError,
        FinancialProjectionError,
        MonthCoverageError,
        StatisticsSerializationError,
        TransactionDataError,
        TransactionResolutionError,
        StatisticsGenerationRollbackError,
        OSError,
    ) as exc:
        raise SystemExit(f"Spending statistics generation failed: {exc}") from exc
    print(format_statistics_generation_report(summary))


if __name__ == "__main__":
    main()
