from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from family_spending.enrichment import TransactionEnrichment
from family_spending.financial_projection import (
    FinancialProjection,
    build_financial_projection,
    write_financial_projection,
)
from family_spending.infrastructure.file_uow import (
    FileUnitOfWork,
    FileUnitOfWorkRollbackError,
)
from family_spending.month_coverage import load_month_coverage
from family_spending.refund_reconciliation import reconcile_refunds
from family_spending.settings import FINANCIAL_SUMMARY_FILE
from family_spending.source_records import SourceRecord
from family_spending.spending_statistics import aggregate_spending
from family_spending.statistics_serialization import (
    serialize_spending_statistics,
    write_spending_statistics_json,
)
from family_spending.transactions import Transaction


@dataclass(frozen=True)
class SpendingProjectionSummary:
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


@dataclass(frozen=True)
class SpendingProjection:
    summary: SpendingProjectionSummary
    payload: dict[str, Any]
    financial_projection: FinancialProjection | None = None


def build_spending_projection(
    transactions: tuple[Transaction, ...],
    transactions_by_id: Mapping[str, Transaction],
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]],
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment],
    emails_dir: Path,
) -> SpendingProjection:
    """Rebuild all spending-dependent projections without touching source or identity stages."""
    refund_result = reconcile_refunds(
        transactions,
        source_records_by_transaction_id,
        enrichments_by_transaction_id,
    )
    statistics = aggregate_spending(
        refund_result.net_consumption,
        transactions_by_id,
        enrichments_by_transaction_id,
    )
    month_coverage = load_month_coverage(
        tuple(month.month for month in statistics.months),
        emails_dir,
    )
    payload = serialize_spending_statistics(statistics, month_coverage)
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
        enrichments_by_transaction_id[item.transaction_id].is_unclassified
        for item in refund_result.net_consumption
    )
    summary = SpendingProjectionSummary(
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
    )
    financial_projection = build_financial_projection(
        transactions,
        transactions_by_id,
        source_records_by_transaction_id,
        enrichments_by_transaction_id,
        emails_dir,
    )
    return SpendingProjection(
        summary=summary,
        payload=payload,
        financial_projection=financial_projection,
    )


def persist_spending_projection(
    projection: SpendingProjection,
    output_path: Path,
    *,
    financial_output_path: Path | None = None,
) -> None:
    """Write projection files inside an already-owned commit boundary."""
    financial_projection = projection.financial_projection
    write_spending_statistics_json(projection.payload, output_path)
    if financial_projection is None:
        return
    if financial_output_path is None:
        financial_output_path = output_path.with_name(FINANCIAL_SUMMARY_FILE.name)
    write_financial_projection(financial_projection, financial_output_path)


def write_spending_projection(
    projection: SpendingProjection,
    output_path: Path,
    *,
    financial_output_path: Path | None = None,
) -> None:
    """Persist spending and its financial sidecar through one shared file unit of work."""
    financial_projection = projection.financial_projection
    if financial_projection is None:
        write_spending_statistics_json(projection.payload, output_path)
        return

    if financial_output_path is None:
        financial_output_path = output_path.with_name(FINANCIAL_SUMMARY_FILE.name)
    try:
        with FileUnitOfWork(
            (output_path, financial_output_path),
            label="Projection write",
        ) as unit_of_work:
            persist_spending_projection(
                projection,
                output_path,
                financial_output_path=financial_output_path,
            )
            unit_of_work.commit()
    except FileUnitOfWorkRollbackError as exc:
        raise OSError(str(exc)) from exc
