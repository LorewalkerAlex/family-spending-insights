from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from family_spending.ingestion.cmb_email_transactions import CmbTransactionCsvError, read_transactions_csv
from family_spending.manual_source import (
    MANUAL_SOURCE_RECORDS_FILE,
    ManualSourceDataError,
    ManualSourceEntry,
    create_manual_source_entry,
    read_manual_source_entries,
    write_manual_source_entries,
)
from family_spending.mapping import MappingDataError, MappingResolutionError, load_merchant_mappings
from family_spending.month_coverage import MonthCoverageError
from family_spending.reconciliation import ReconciliationError
from family_spending.refund_reconciliation import RefundReconciliationError
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
    SourceLinkStoreError,
    read_transaction_source_links,
    write_transaction_source_links,
)
from family_spending.source_records import TransactionType
from family_spending.spending_statistics import SpendingStatisticsError
from family_spending.statistics_generation import generate_spending_statistics
from family_spending.statistics_serialization import StatisticsSerializationError
from family_spending.transaction_resolution import TransactionResolutionError, build_household_domain_state
from family_spending.transactions import TransactionDataError


@dataclass(frozen=True)
class ManualInputResult:
    source_record_id: str
    transaction_id: str
    action: str


def _parse_date(value: str) -> date:
    """Require ISO dates so CLI input has the same day precision as Transaction v1."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def _parse_decimal(value: str) -> Decimal:
    """Parse amounts as Decimal so Manual Source never introduces binary-float rounding."""
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid amount {value!r}") from exc
    if not amount.is_finite():
        raise argparse.ArgumentTypeError(f"invalid amount {value!r}")
    return amount


def submit_manual_input(
    entry: ManualSourceEntry,
    *,
    transactions_path: Path = TRANSACTIONS_FILE,
    manual_source_path: Path = MANUAL_SOURCE_RECORDS_FILE,
    source_links_path: Path = TRANSACTION_SOURCE_LINKS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    overrides_path: Path = TRANSACTION_CATEGORY_OVERRIDES_FILE,
    output_path: Path = SPENDING_STATISTICS_FILE,
    emails_dir: Path = EMAILS_DIR,
) -> ManualInputResult:
    """Validate Manual input against current sources, persist it, then immediately run the downstream statistics pipeline."""
    raw_cmb = read_transactions_csv(transactions_path)
    existing_manual = read_manual_source_entries(manual_source_path)
    existing_links = read_transaction_source_links(source_links_path)
    mappings = load_merchant_mappings(merchants_path, categories_path, overrides_path)

    candidate_entries = existing_manual + (entry,)
    state = build_household_domain_state(
        raw_cmb,
        candidate_entries,
        mappings,
        existing_links=existing_links,
    )
    decision = next(
        item for item in state.reconciliation.decisions if item.source_record_id == entry.id
    )

    # Persist only after reconciliation and Enrichment validation succeeds. The following
    # downstream rebuild uses the same source files and relation state just validated above.
    write_manual_source_entries(candidate_entries, manual_source_path)
    write_transaction_source_links(state.reconciliation.source_links, source_links_path)
    generate_spending_statistics(
        transactions_path,
        merchants_path,
        categories_path,
        overrides_path,
        output_path,
        emails_dir,
        manual_source_path,
        source_links_path,
    )
    return ManualInputResult(
        source_record_id=entry.id,
        transaction_id=decision.transaction_id,
        action=decision.action,
    )


def build_parser() -> argparse.ArgumentParser:
    """Keep the first Manual entry surface intentionally small while exercising the real backend pipeline."""
    parser = argparse.ArgumentParser(description="Add one Manual Source financial record")
    parser.add_argument("--type", dest="transaction_type", choices=("income", "expense"), required=True)
    parser.add_argument("--date", dest="transaction_date", type=_parse_date, required=True)
    parser.add_argument("--amount", type=_parse_decimal, required=True)
    parser.add_argument("--merchant")
    parser.add_argument("--category")
    parser.add_argument("--note")
    return parser


def main() -> None:
    """Expose a minimal local entrypoint that proves Manual Source can drive the same downstream pipeline as CMB."""
    args = build_parser().parse_args()
    entry = create_manual_source_entry(
        transaction_type=args.transaction_type,
        transaction_date=args.transaction_date,
        amount=args.amount,
        merchant_name=args.merchant,
        category=args.category,
        note=args.note,
    )
    try:
        result = submit_manual_input(entry)
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
        raise SystemExit(f"Manual input failed: {exc}") from exc
    print(
        f"Manual input accepted: source_record_id={result.source_record_id} | "
        f"transaction_id={result.transaction_id} | action={result.action}"
    )


if __name__ == "__main__":
    main()
