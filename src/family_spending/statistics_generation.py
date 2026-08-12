from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from family_spending.enrichment import (
    INCOME_DEFAULT_CATEGORY,
    TransactionEnrichmentState,
    materialize_enrichment_state,
)
from family_spending.enrichment_store import (
    EnrichmentStateStoreError,
    read_enrichment_states,
    write_enrichment_states,
)
from family_spending.financial_projection import FinancialProjectionError
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransactionCsvError,
    read_transactions_csv,
)
from family_spending.manual_source import (
    ManualSourceDataError,
    read_manual_source_entries,
)
from family_spending.mapping import MappingDataError, MappingResolutionError, load_merchant_mappings
from family_spending.month_coverage import MonthCoverageError
from family_spending.reconciliation import ReconciliationError
from family_spending.refund_reconciliation import RefundReconciliationError
from family_spending.settings import (
    CATEGORIES_FILE,
    EMAILS_DIR,
    FINANCIAL_SUMMARY_FILE,
    MERCHANTS_FILE,
    SPENDING_STATISTICS_FILE,
    TRANSACTIONS_FILE,
)
from family_spending.source_link_store import (
    SourceLinkStoreError,
    read_transaction_source_links,
    write_transaction_source_links,
)
from family_spending.spending_projection import (
    build_spending_projection,
    write_spending_projection,
)
from family_spending.spending_statistics import SpendingStatisticsError
from family_spending.statistics_serialization import StatisticsSerializationError
from family_spending.transaction_resolution import (
    TransactionResolutionError,
    build_household_domain_state,
)
from family_spending.transactions import Transaction, TransactionDataError


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
    """Raised when a failed multi-file projection persistence cannot restore its prior state."""


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    contents: bytes | None


def _snapshot_file(path: Path) -> _FileSnapshot:
    return _FileSnapshot(path=path, contents=path.read_bytes() if path.exists() else None)


def _restore_file(snapshot: _FileSnapshot) -> None:
    if snapshot.contents is None:
        snapshot.path.unlink(missing_ok=True)
        return
    snapshot.path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{snapshot.path.name}.", suffix=".rollback", dir=snapshot.path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(snapshot.contents)
        os.replace(temp_name, snapshot.path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _restore_snapshots(
    snapshots: tuple[_FileSnapshot, ...], original_error: Exception
) -> None:
    failures: list[str] = []
    for snapshot in reversed(snapshots):
        try:
            _restore_file(snapshot)
        except Exception as exc:  # pragma: no cover - requires secondary storage failure
            failures.append(f"{snapshot.path}: {exc}")
    if failures:
        raise StatisticsGenerationRollbackError(
            "Statistics generation failed and rollback could not fully restore persisted state: "
            + "; ".join(failures)
        ) from original_error


def _default_sibling(path: Path, filename: str) -> Path:
    """Keep test and alternate data roots isolated by deriving new local state beside the selected CMB CSV."""
    return path.parent / filename


def _normalize_legacy_income_states(
    transactions: tuple[Transaction, ...],
    states: tuple[TransactionEnrichmentState, ...],
) -> tuple[TransactionEnrichmentState, ...]:
    """Migrate only implicit pre-Income states while preserving explicit historical Enrichment decisions."""
    transactions_by_id = {transaction.id: transaction for transaction in transactions}
    normalized: list[TransactionEnrichmentState] = []
    for state in states:
        transaction = transactions_by_id[state.transaction_id]
        if (
            transaction.transaction_type == "income"
            and state.category_source in ("merchant_default", "unclassified")
        ):
            state = TransactionEnrichmentState(
                transaction_id=state.transaction_id,
                merchant_name=None,
                default_category=None,
                category=INCOME_DEFAULT_CATEGORY,
                category_source="income_default",
                note=state.note,
            )
        normalized.append(state)
    return tuple(normalized)


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
    """Rebuild spending plus household financial projections from current formal state."""
    if manual_source_path is None:
        manual_source_path = _default_sibling(transactions_path, "manual_source_records.jsonl")
    if source_links_path is None:
        source_links_path = _default_sibling(transactions_path, "transaction_source_links.jsonl")
    if enrichment_state_path is None:
        enrichment_state_path = _default_sibling(transactions_path, "enrichment_state.jsonl")
    if financial_output_path is None:
        financial_output_path = output_path.with_name(FINANCIAL_SUMMARY_FILE.name)
    raw_transactions = read_transactions_csv(transactions_path)
    manual_entries = read_manual_source_entries(manual_source_path)
    existing_links = read_transaction_source_links(source_links_path)
    existing_enrichment_states = read_enrichment_states(enrichment_state_path)
    mappings = load_merchant_mappings(merchants_path, categories_path)
    state = build_household_domain_state(
        raw_transactions,
        manual_entries,
        mappings,
        existing_links=existing_links,
        existing_enrichment_states={
            item.transaction_id: item for item in existing_enrichment_states
        },
    )
    enrichment_states = _normalize_legacy_income_states(
        state.reconciliation.transactions,
        state.enrichment_states,
    )
    enrichment_states_by_id = {
        item.transaction_id: item for item in enrichment_states
    }
    enrichments_by_id = MappingProxyType(
        {
            transaction.id: materialize_enrichment_state(
                enrichment_states_by_id[transaction.id],
                state.source_records_by_transaction_id[transaction.id],
            )
            for transaction in state.reconciliation.transactions
        }
    )
    spending_projection = build_spending_projection(
        state.reconciliation.transactions,
        state.transactions_by_id,
        state.source_records_by_transaction_id,
        enrichments_by_id,
        emails_dir,
    )
    # Persist current identity/state and both projections as one recoverable local commit.
    persisted_paths = (
        source_links_path,
        enrichment_state_path,
        output_path,
        financial_output_path,
    )
    snapshots = tuple(_snapshot_file(path) for path in persisted_paths)
    try:
        write_transaction_source_links(state.reconciliation.source_links, source_links_path)
        write_enrichment_states(enrichment_states, enrichment_state_path)
        write_spending_projection(
            spending_projection,
            output_path,
            financial_output_path=financial_output_path,
        )
    except Exception as exc:
        _restore_snapshots(snapshots, exc)
        raise
    summary = spending_projection.summary
    return StatisticsGenerationSummary(
        raw_transactions=len(raw_transactions),
        zero_amount_transactions=summary.zero_amount_transactions,
        refund_transactions=summary.refund_transactions,
        same_merchant_refund_matches=summary.same_merchant_refund_matches,
        same_merchant_matched_amount=summary.same_merchant_matched_amount,
        net_consumption_transactions=summary.net_consumption_transactions,
        fully_refunded_transactions=summary.fully_refunded_transactions,
        partially_refunded_transactions=summary.partially_refunded_transactions,
        unmatched_refund_count=summary.unmatched_refund_count,
        unmatched_refund_amount=summary.unmatched_refund_amount,
        unclassified_net_transactions=summary.unclassified_net_transactions,
        months=summary.months,
        total_net_spending=summary.total_net_spending,
        shown_months=summary.shown_months,
        shown_net_spending=summary.shown_net_spending,
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
