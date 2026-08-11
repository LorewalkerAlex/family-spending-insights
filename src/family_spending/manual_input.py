from __future__ import annotations

import argparse
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from family_spending.enrichment import (
    update_merchant_enrichment_state,
    update_note_enrichment_state,
)
from family_spending.enrichment_store import (
    EnrichmentStateStoreError,
    read_enrichment_states,
    write_enrichment_states,
)
from family_spending.ingestion.cmb_email_transactions import CmbTransactionCsvError, read_transactions_csv
from family_spending.manual_source import (
    MANUAL_SOURCE_RECORDS_FILE,
    ManualSourceDataError,
    ManualSourceEntry,
    create_manual_source_entry,
    read_manual_source_entries,
    write_manual_source_entries,
)
from family_spending.mapping import (
    MappingDataError,
    MappingResolutionError,
    load_merchant_mappings,
)
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
from family_spending.transactions import TransactionDataError, TransactionSourceLink


class ManualInputRollbackError(RuntimeError):
    """Raised when a failed Manual Input cannot restore all files to their pre-command state."""


@dataclass(frozen=True)
class ManualInputResult:
    source_record_id: str
    transaction_id: str
    action: str


@dataclass(frozen=True)
class ManualInputDeletionResult:
    source_record_id: str
    transaction_id: str
    transaction_removed: bool


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    contents: bytes | None


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


def _snapshot_file(path: Path) -> _FileSnapshot:
    """Capture exact bytes so rollback can restore every persisted participant."""
    return _FileSnapshot(path=path, contents=path.read_bytes() if path.exists() else None)


def _restore_file(snapshot: _FileSnapshot) -> None:
    """Restore one file atomically, or remove it if it did not exist before the command."""
    path = snapshot.path
    if snapshot.contents is None:
        path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".rollback",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(snapshot.contents)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _restore_snapshots(snapshots: tuple[_FileSnapshot, ...], original_error: Exception) -> None:
    """Restore snapshots in reverse mutation order and surface any secondary storage failure."""
    failures: list[str] = []
    for snapshot in reversed(snapshots):
        try:
            _restore_file(snapshot)
        except Exception as exc:  # pragma: no cover - requires a second storage failure during rollback
            failures.append(f"{snapshot.path}: {exc}")
    if failures:
        raise ManualInputRollbackError(
            "Manual input failed and rollback could not fully restore persisted state: "
            + "; ".join(failures)
        ) from original_error


def _persist_manual_state(
    *,
    manual_entries: tuple[ManualSourceEntry, ...],
    source_links: tuple,
    enrichment_states: tuple,
    transactions_path: Path,
    manual_source_path: Path,
    source_links_path: Path,
    merchants_path: Path,
    categories_path: Path,
    output_path: Path,
    emails_dir: Path,
    enrichment_state_path: Path,
) -> None:
    """Persist one Manual Source mutation with the same rollback boundary as create."""
    snapshots = tuple(
        _snapshot_file(path)
        for path in (
            manual_source_path,
            source_links_path,
            enrichment_state_path,
            output_path,
        )
    )
    try:
        write_manual_source_entries(manual_entries, manual_source_path)
        write_transaction_source_links(source_links, source_links_path)
        write_enrichment_states(enrichment_states, enrichment_state_path)
        generate_spending_statistics(
            transactions_path=transactions_path,
            merchants_path=merchants_path,
            categories_path=categories_path,
            output_path=output_path,
            emails_dir=emails_dir,
            manual_source_path=manual_source_path,
            source_links_path=source_links_path,
            enrichment_state_path=enrichment_state_path,
        )
    except Exception as exc:
        _restore_snapshots(snapshots, exc)
        raise


def submit_manual_input(
    entry: ManualSourceEntry,
    *,
    transactions_path: Path = TRANSACTIONS_FILE,
    manual_source_path: Path = MANUAL_SOURCE_RECORDS_FILE,
    source_links_path: Path = TRANSACTION_SOURCE_LINKS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    output_path: Path = SPENDING_STATISTICS_FILE,
    emails_dir: Path = EMAILS_DIR,
    enrichment_state_path: Path | None = None,
) -> ManualInputResult:
    """Validate and persist one Manual Source command, rolling back all touched files on failure."""
    if enrichment_state_path is None:
        enrichment_state_path = transactions_path.parent / "enrichment_state.jsonl"
    raw_cmb = read_transactions_csv(transactions_path)
    existing_manual = read_manual_source_entries(manual_source_path)
    existing_links = read_transaction_source_links(source_links_path)
    existing_enrichment_states = read_enrichment_states(enrichment_state_path)
    mappings = load_merchant_mappings(merchants_path, categories_path)
    candidate_entries = existing_manual + (entry,)
    state = build_household_domain_state(
        raw_cmb,
        candidate_entries,
        mappings,
        existing_links=existing_links,
        existing_enrichment_states={
            item.transaction_id: item for item in existing_enrichment_states
        },
    )
    decision = next(
        item for item in state.reconciliation.decisions if item.source_record_id == entry.id
    )
    updated_state = state.enrichment_states_by_transaction_id[decision.transaction_id]
    if entry.note is not None:
        updated_state = update_note_enrichment_state(updated_state, entry.note)
    updated_enrichment_states = tuple(
        updated_state if item.transaction_id == decision.transaction_id else item
        for item in state.enrichment_states
    )

    # Manual Source stays source-native. Note is copied into current Enrichment only for this
    # explicit command; Merchant/Category continue to come from the shared Mapping path.
    _persist_manual_state(
        manual_entries=candidate_entries,
        source_links=state.reconciliation.source_links,
        enrichment_states=updated_enrichment_states,
        transactions_path=transactions_path,
        manual_source_path=manual_source_path,
        source_links_path=source_links_path,
        merchants_path=merchants_path,
        categories_path=categories_path,
        output_path=output_path,
        emails_dir=emails_dir,
        enrichment_state_path=enrichment_state_path,
    )

    return ManualInputResult(
        source_record_id=entry.id,
        transaction_id=decision.transaction_id,
        action=decision.action,
    )


def replace_manual_input(
    source_record_id: str,
    replacement: ManualSourceEntry,
    *,
    transactions_path: Path = TRANSACTIONS_FILE,
    manual_source_path: Path = MANUAL_SOURCE_RECORDS_FILE,
    source_links_path: Path = TRANSACTION_SOURCE_LINKS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    output_path: Path = SPENDING_STATISTICS_FILE,
    emails_dir: Path = EMAILS_DIR,
    enrichment_state_path: Path | None = None,
    update_note: bool = True,
) -> ManualInputResult:
    """Replace one Manual Source fact with a new Source identity and rerun reconciliation."""
    if replacement.id == source_record_id:
        raise ManualSourceDataError("Manual input correction must create a new source record id")
    if enrichment_state_path is None:
        enrichment_state_path = transactions_path.parent / "enrichment_state.jsonl"

    raw_cmb = read_transactions_csv(transactions_path)
    existing_manual = read_manual_source_entries(manual_source_path)
    replacement_index = next(
        (index for index, item in enumerate(existing_manual) if item.id == source_record_id),
        None,
    )
    if replacement_index is None:
        raise ManualSourceDataError(f"Manual source record {source_record_id!r} does not exist")
    if any(item.id == replacement.id for item in existing_manual):
        raise ManualSourceDataError(f"Manual source record {replacement.id!r} already exists")

    candidate_entries = list(existing_manual)
    candidate_entries[replacement_index] = replacement
    candidate_tuple = tuple(candidate_entries)
    existing_links = read_transaction_source_links(source_links_path)
    existing_enrichment_states = read_enrichment_states(enrichment_state_path)
    mappings = load_merchant_mappings(merchants_path, categories_path)
    persisted_enrichment_by_id = {
        item.transaction_id: item for item in existing_enrichment_states
    }
    provisional_state = build_household_domain_state(
        raw_cmb,
        candidate_tuple,
        mappings,
        existing_links=existing_links,
        existing_enrichment_states=persisted_enrichment_by_id,
    )
    provisional_decision = next(
        item
        for item in provisional_state.reconciliation.decisions
        if item.source_record_id == replacement.id
    )
    previous_link = next(
        (item for item in existing_links if item.source_record_id == source_record_id),
        None,
    )
    if previous_link is None:
        raise ManualSourceDataError(
            f"Manual source record {source_record_id!r} has no current Transaction link"
        )

    state = provisional_state
    decision = provisional_decision
    preserved_transaction_identity = False
    if (
        previous_link.role == "authoritative"
        and previous_link.transaction_id not in provisional_state.transactions_by_id
        and provisional_decision.action == "created"
    ):
        # A source correction changes Source identity, not real-world Transaction identity.
        # If the corrected record still does not match another current Transaction, carry
        # the existing Transaction relation forward so persisted Enrichment remains attached.
        remapped_links = tuple(
            TransactionSourceLink(
                transaction_id=item.transaction_id,
                source_record_id=(
                    replacement.id
                    if item.source_record_id == source_record_id
                    else item.source_record_id
                ),
                role=item.role,
            )
            for item in existing_links
        )
        state = build_household_domain_state(
            raw_cmb,
            candidate_tuple,
            mappings,
            existing_links=remapped_links,
            existing_enrichment_states=persisted_enrichment_by_id,
        )
        decision = next(
            item
            for item in state.reconciliation.decisions
            if item.source_record_id == replacement.id
        )
        preserved_transaction_identity = True

    corrected_state = state.enrichment_states_by_transaction_id[decision.transaction_id]
    if preserved_transaction_identity:
        previous_entry = existing_manual[replacement_index]
        previous_mapping_merchant = (
            mappings.description_to_merchant.get(previous_entry.description)
            if previous_entry.description is not None
            else None
        )
        if corrected_state.merchant_name == previous_mapping_merchant:
            replacement_merchant = (
                mappings.description_to_merchant.get(replacement.description)
                if replacement.description is not None
                else None
            )
            replacement_default = (
                mappings.merchant_to_category.get(replacement_merchant)
                if replacement_merchant is not None
                else None
            )
            corrected_state = update_merchant_enrichment_state(
                corrected_state,
                merchant_name=replacement_merchant,
                default_category=replacement_default,
            )
    if update_note:
        # Note is Enrichment, not Transaction Core. Only an explicitly supplied correction
        # should overwrite a persisted Note on an already-known Transaction.
        corrected_state = update_note_enrichment_state(corrected_state, replacement.note)
    corrected_states = tuple(
        corrected_state if item.transaction_id == decision.transaction_id else item
        for item in state.enrichment_states
    )

    _persist_manual_state(
        manual_entries=candidate_tuple,
        source_links=state.reconciliation.source_links,
        enrichment_states=corrected_states,
        transactions_path=transactions_path,
        manual_source_path=manual_source_path,
        source_links_path=source_links_path,
        merchants_path=merchants_path,
        categories_path=categories_path,
        output_path=output_path,
        emails_dir=emails_dir,
        enrichment_state_path=enrichment_state_path,
    )
    return ManualInputResult(
        source_record_id=replacement.id,
        transaction_id=decision.transaction_id,
        action=decision.action,
    )


def delete_manual_input(
    source_record_id: str,
    *,
    transactions_path: Path = TRANSACTIONS_FILE,
    manual_source_path: Path = MANUAL_SOURCE_RECORDS_FILE,
    source_links_path: Path = TRANSACTION_SOURCE_LINKS_FILE,
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    output_path: Path = SPENDING_STATISTICS_FILE,
    emails_dir: Path = EMAILS_DIR,
    enrichment_state_path: Path | None = None,
) -> ManualInputDeletionResult:
    """Delete one Manual Source fact and let current source authority decide whether its Transaction survives."""
    if enrichment_state_path is None:
        enrichment_state_path = transactions_path.parent / "enrichment_state.jsonl"

    raw_cmb = read_transactions_csv(transactions_path)
    existing_manual = read_manual_source_entries(manual_source_path)
    if not any(item.id == source_record_id for item in existing_manual):
        raise ManualSourceDataError(f"Manual source record {source_record_id!r} does not exist")
    existing_links = read_transaction_source_links(source_links_path)
    previous_link = next(
        (item for item in existing_links if item.source_record_id == source_record_id),
        None,
    )
    if previous_link is None:
        raise ManualSourceDataError(
            f"Manual source record {source_record_id!r} has no current Transaction link"
        )

    candidate_entries = tuple(item for item in existing_manual if item.id != source_record_id)
    existing_enrichment_states = read_enrichment_states(enrichment_state_path)
    mappings = load_merchant_mappings(merchants_path, categories_path)
    state = build_household_domain_state(
        raw_cmb,
        candidate_entries,
        mappings,
        existing_links=existing_links,
        existing_enrichment_states={
            item.transaction_id: item for item in existing_enrichment_states
        },
    )
    transaction_removed = previous_link.transaction_id not in state.transactions_by_id

    _persist_manual_state(
        manual_entries=candidate_entries,
        source_links=state.reconciliation.source_links,
        enrichment_states=state.enrichment_states,
        transactions_path=transactions_path,
        manual_source_path=manual_source_path,
        source_links_path=source_links_path,
        merchants_path=merchants_path,
        categories_path=categories_path,
        output_path=output_path,
        emails_dir=emails_dir,
        enrichment_state_path=enrichment_state_path,
    )
    return ManualInputDeletionResult(
        source_record_id=source_record_id,
        transaction_id=previous_link.transaction_id,
        transaction_removed=transaction_removed,
    )


def build_parser() -> argparse.ArgumentParser:
    """Keep the first Manual entry surface intentionally small while exercising the real backend pipeline."""
    parser = argparse.ArgumentParser(description="Add one Manual Source financial record")
    parser.add_argument("--type", dest="transaction_type", choices=("income", "expense"), required=True)
    parser.add_argument("--date", dest="transaction_date", type=_parse_date, required=True)
    parser.add_argument("--amount", type=_parse_decimal, required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--note")
    return parser


def main() -> None:
    """Expose a minimal local entrypoint that proves Manual Source can drive the same downstream pipeline as CMB."""
    args = build_parser().parse_args()
    entry = create_manual_source_entry(
        transaction_type=args.transaction_type,
        transaction_date=args.transaction_date,
        amount=args.amount,
        description=args.description,
        note=args.note,
    )
    try:
        result = submit_manual_input(entry)
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
        MonthCoverageError,
        StatisticsSerializationError,
        TransactionDataError,
        TransactionResolutionError,
        ManualInputRollbackError,
        OSError,
    ) as exc:
        raise SystemExit(f"Manual input failed: {exc}") from exc
    print(
        f"Manual input accepted: source_record_id={result.source_record_id} | "
        f"transaction_id={result.transaction_id} | action={result.action}"
    )


if __name__ == "__main__":
    main()
