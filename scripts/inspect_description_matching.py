from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from loguru import logger

from inspect_mapping_candidates import (
    OCRRow,
    Transaction,
    normalize_text,
    read_ocr_rows,
    read_transactions,
)

SOURCE_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})(?:_|$)")


@dataclass(frozen=True)
class Candidate:
    transaction: Transaction
    ocr: OCRRow
    occurred_on: date
    merchant_key: str
    day_delta: int


@dataclass(frozen=True)
class Assignment:
    processing_order: int
    description: str
    suggested_merchant: str
    transaction: Transaction
    ocr: OCRRow
    occurred_on: date
    match_stage: str
    day_delta: int


@dataclass(frozen=True)
class GroupResult:
    processing_order: int
    description: str
    transaction_count: int
    covered_transaction_count: int
    eligible_evidence_transaction_count: int
    seed_transaction_count: int
    seed_merchants: tuple[tuple[str, int], ...]
    seed_stage: str | None
    mapping_confidence: str | None
    suggested_merchant: str | None
    status: str
    ocr_evidence_assignment_count: int
    signed_same_day_assignment_count: int
    absolute_same_day_assignment_count: int
    absolute_window_assignment_count: int
    mapped_transaction_count: int
    mapping_only_transaction_count: int
    unresolved_mapping_transaction_count: int
    refund_mapping_only_count: int
    coverage_mapping_only_count: int
    no_evidence_mapping_only_count: int
    candidate_not_assigned_mapping_only_count: int
    ocr_available_before: int
    ocr_consumed: int
    ocr_available_after: int

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate description-first merchant matching without modifying OCR, "
            "transactions, or formal Mapping data."
        )
    )
    parser.add_argument(
        "--transactions",
        type=Path,
        default=Path("data/transactions.csv"),
    )
    parser.add_argument(
        "--ocr",
        type=Path,
        default=Path("tmp/app_row_ocr_inspection/ocr_results.jsonl"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tmp/description_matching_inspection"),
    )
    parser.add_argument("--example-limit", type=int, default=30)
    parser.add_argument(
        "--secondary-day-window",
        type=int,
        default=7,
        help=(
            "Maximum date distance for description-guided relaxed matching after "
            "a merchant hypothesis has been established."
        ),
    )
    return parser.parse_args()

def infer_ocr_date(row: OCRRow) -> date | None:
    if row.month_day is None:
        return None

    match = SOURCE_MONTH_RE.match(row.source_stem)
    if match is None:
        return None

    source_year = int(match.group("year"))
    source_month = int(match.group("month"))
    source_month_start = date(source_year, source_month, 1)
    month, day = row.month_day

    candidates: list[date] = []
    for year in range(source_year - 1, source_year + 2):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue

    if not candidates:
        return None
    return min(
        candidates,
        key=lambda candidate: (
            abs((candidate - source_month_start).days),
            candidate,
        ),
    )


def build_ocr_dates(ocr_rows: list[OCRRow]) -> dict[str, date | None]:
    return {row.row_image: infer_ocr_date(row) for row in ocr_rows}


def build_amount_index(ocr_rows: list[OCRRow]) -> dict[Decimal, list[OCRRow]]:
    index: dict[Decimal, list[OCRRow]] = defaultdict(list)
    for row in ocr_rows:
        if row.amount is not None:
            index[row.amount].append(row)
    return index


def build_absolute_amount_index(
    ocr_rows: list[OCRRow],
) -> dict[Decimal, list[OCRRow]]:
    index: dict[Decimal, list[OCRRow]] = defaultdict(list)
    for row in ocr_rows:
        if row.amount is not None:
            index[abs(row.amount)].append(row)
    return index

def merchant_display_names(ocr_rows: list[OCRRow]) -> dict[str, str]:
    variants: dict[str, Counter[str]] = defaultdict(Counter)
    for row in ocr_rows:
        key = normalize_text(row.merchant_text)
        if key:
            variants[key][row.merchant_text] += 1
    return {
        key: counts.most_common(1)[0][0]
        for key, counts in variants.items()
    }


def candidate_sort_key(candidate: Candidate) -> tuple[object, ...]:
    return (
        abs(candidate.day_delta),
        candidate.occurred_on,
        candidate.ocr.source_stem,
        candidate.ocr.row_index,
        candidate.ocr.row_image,
    )


def build_candidates(
    transactions: list[Transaction],
    amount_index: dict[Decimal, list[OCRRow]],
    absolute_amount_index: dict[Decimal, list[OCRRow]],
    ocr_dates: dict[str, date | None],
    available_ocr: set[str],
    *,
    amount_mode: str,
    max_abs_day_delta: int,
    merchant_key: str | None = None,
    include_same_day: bool = True,
) -> dict[str, list[Candidate]]:
    if amount_mode not in {"opposite", "absolute"}:
        raise ValueError(f"Unsupported amount mode: {amount_mode}")

    candidates: dict[str, list[Candidate]] = defaultdict(list)
    for transaction in transactions:
        if amount_mode == "opposite":
            rows = amount_index.get(-transaction.amount, [])
        else:
            rows = absolute_amount_index.get(abs(transaction.amount), [])

        for row in rows:
            if row.row_image not in available_ocr:
                continue
            occurred_on = ocr_dates[row.row_image]
            if occurred_on is None:
                continue
            day_delta = (occurred_on - transaction.transaction_date).days
            if abs(day_delta) > max_abs_day_delta:
                continue
            if not include_same_day and day_delta == 0:
                continue
            row_merchant_key = normalize_text(row.merchant_text)
            if not row_merchant_key:
                continue
            if merchant_key is not None and row_merchant_key != merchant_key:
                continue
            candidates[transaction.transaction_id].append(
                Candidate(
                    transaction=transaction,
                    ocr=row,
                    occurred_on=occurred_on,
                    merchant_key=row_merchant_key,
                    day_delta=day_delta,
                )
            )

    for transaction_candidates in candidates.values():
        transaction_candidates.sort(key=candidate_sort_key)
    return candidates

def seed_merchant_counts(
    transactions: list[Transaction],
    candidates: dict[str, list[Candidate]],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for transaction in transactions:
        merchant_keys = {
            candidate.merchant_key
            for candidate in candidates.get(transaction.transaction_id, [])
        }
        if len(merchant_keys) == 1:
            counts[next(iter(merchant_keys))] += 1
    return counts


def transaction_sort_key(transaction: Transaction) -> tuple[object, ...]:
    return (
        transaction.transaction_date,
        transaction.source_email,
        transaction.source_index,
        transaction.transaction_id,
    )


def transaction_kind(transaction: Transaction) -> str:
    if transaction.amount < 0:
        return "refund"
    if transaction.amount > 0:
        return "purchase"
    return "zero"


def select_one_to_one_candidates(
    transactions: list[Transaction],
    candidates: dict[str, list[Candidate]],
) -> dict[str, Candidate]:
    row_to_transaction: dict[str, str] = {}
    edge_lookup: dict[tuple[str, str], Candidate] = {}
    transactions_by_id = {
        transaction.transaction_id: transaction for transaction in transactions
    }
    for transaction in transactions:
        for candidate in candidates.get(transaction.transaction_id, []):
            edge_lookup[(transaction.transaction_id, candidate.ocr.row_image)] = candidate

    def try_assign(transaction_id: str, seen_rows: set[str]) -> bool:
        for candidate in candidates.get(transaction_id, []):
            row_image = candidate.ocr.row_image
            if row_image in seen_rows:
                continue
            seen_rows.add(row_image)
            current_transaction_id = row_to_transaction.get(row_image)
            if current_transaction_id is None or try_assign(
                current_transaction_id, seen_rows
            ):
                row_to_transaction[row_image] = transaction_id
                return True
        return False

    for transaction in sorted(transactions, key=transaction_sort_key):
        try_assign(transaction.transaction_id, set())

    selected: dict[str, Candidate] = {}
    for row_image, transaction_id in row_to_transaction.items():
        if transaction_id not in transactions_by_id:
            continue
        selected[transaction_id] = edge_lookup[(transaction_id, row_image)]
    return selected


def assign_candidates(
    processing_order: int,
    description: str,
    suggested_merchant: str,
    transactions: list[Transaction],
    candidates: dict[str, list[Candidate]],
    match_stage: str,
) -> list[Assignment]:
    selected = select_one_to_one_candidates(transactions, candidates)
    assignments: list[Assignment] = []
    for transaction in sorted(transactions, key=transaction_sort_key):
        candidate = selected.get(transaction.transaction_id)
        if candidate is None:
            continue
        assignments.append(
            Assignment(
                processing_order=processing_order,
                description=description,
                suggested_merchant=suggested_merchant,
                transaction=transaction,
                ocr=candidate.ocr,
                occurred_on=candidate.occurred_on,
                match_stage=match_stage,
                day_delta=candidate.day_delta,
            )
        )
    return assignments

def group_transactions(transactions: list[Transaction]) -> list[tuple[str, list[Transaction]]]:
    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        grouped[transaction.description].append(transaction)
    return sorted(
        grouped.items(),
        key=lambda item: (
            -len(item[1]),
            min(transaction.transaction_date for transaction in item[1]),
            item[0],
        ),
    )


def process_groups(
    transactions: list[Transaction],
    ocr_rows: list[OCRRow],
    secondary_day_window: int,
) -> tuple[
    list[GroupResult],
    list[Assignment],
    dict[str, list[Candidate]],
    set[str],
    date | None,
    date | None,
]:
    ocr_dates = build_ocr_dates(ocr_rows)
    dated_ocr = [value for value in ocr_dates.values() if value is not None]
    coverage_start = min(dated_ocr) if dated_ocr else None
    coverage_end = max(dated_ocr) if dated_ocr else None
    amount_index = build_amount_index(ocr_rows)
    absolute_amount_index = build_absolute_amount_index(ocr_rows)
    display_names = merchant_display_names(ocr_rows)
    available_ocr = {row.row_image for row in ocr_rows}

    results: list[GroupResult] = []
    assignments: list[Assignment] = []
    unresolved_candidates: dict[str, list[Candidate]] = {}

    for processing_order, (description, group) in enumerate(
        group_transactions(transactions), 1
    ):
        available_before = len(available_ocr)
        covered = [
            transaction
            for transaction in group
            if coverage_start is not None
            and coverage_end is not None
            and coverage_start <= transaction.transaction_date <= coverage_end
        ]
        # Refund rows were excluded when the App screenshots were exported.
        # Only positive bank transactions may establish or consume OCR evidence.
        evidence_transactions = [
            transaction for transaction in covered if transaction.amount > 0
        ]

        signed_same_day_candidates = build_candidates(
            evidence_transactions,
            amount_index,
            absolute_amount_index,
            ocr_dates,
            available_ocr,
            amount_mode="opposite",
            max_abs_day_delta=0,
        )
        signed_seeds = seed_merchant_counts(
            evidence_transactions,
            signed_same_day_candidates,
        )

        absolute_same_day_candidates = build_candidates(
            evidence_transactions,
            amount_index,
            absolute_amount_index,
            ocr_dates,
            available_ocr,
            amount_mode="absolute",
            max_abs_day_delta=0,
        )
        absolute_seeds = seed_merchant_counts(
            evidence_transactions,
            absolute_same_day_candidates,
        )

        merchant_key: str | None = None
        seed_stage: str | None = None
        mapping_confidence: str | None = None
        chosen_seeds: Counter[str] = Counter()

        if len(signed_seeds) == 1:
            merchant_key = next(iter(signed_seeds))
            seed_stage = "signed_same_day"
            mapping_confidence = "high"
            chosen_seeds = signed_seeds
            status = "mapped_signed_same_day_seed"
        elif len(signed_seeds) > 1:
            status = "conflicting_signed_same_day_seeds"
            chosen_seeds = signed_seeds
        elif len(absolute_seeds) == 1:
            merchant_key = next(iter(absolute_seeds))
            seed_stage = "absolute_same_day"
            mapping_confidence = "medium"
            chosen_seeds = absolute_seeds
            status = "mapped_absolute_same_day_seed"
        elif len(absolute_seeds) > 1:
            status = "conflicting_absolute_same_day_seeds"
            chosen_seeds = absolute_seeds
        elif any(absolute_same_day_candidates.values()):
            status = "ambiguous_without_seed"
        elif evidence_transactions:
            status = "no_same_day_candidate"
        elif covered:
            status = "no_eligible_purchase_evidence"
        else:
            status = "ocr_not_covered"

        seed_merchants = tuple(
            sorted(
                (
                    (display_names.get(key, key), count)
                    for key, count in chosen_seeds.items()
                ),
                key=lambda item: (-item[1], item[0]),
            )
        )
        suggested_merchant = (
            display_names.get(merchant_key, merchant_key)
            if merchant_key is not None
            else None
        )

        signed_same_day_assignments: list[Assignment] = []
        absolute_same_day_assignments: list[Assignment] = []
        absolute_window_assignments: list[Assignment] = []

        if merchant_key is not None and suggested_merchant is not None:
            signed_same_day_assignments = assign_candidates(
                processing_order,
                description,
                suggested_merchant,
                evidence_transactions,
                {
                    transaction_id: [
                        candidate
                        for candidate in transaction_candidates
                        if candidate.merchant_key == merchant_key
                    ]
                    for transaction_id, transaction_candidates
                    in signed_same_day_candidates.items()
                },
                "signed_same_day",
            )
            available_ocr.difference_update(
                assignment.ocr.row_image
                for assignment in signed_same_day_assignments
            )

            assigned_ids = {
                assignment.transaction.transaction_id
                for assignment in signed_same_day_assignments
            }
            unresolved = [
                transaction
                for transaction in evidence_transactions
                if transaction.transaction_id not in assigned_ids
            ]
            absolute_same_day_candidates = build_candidates(
                unresolved,
                amount_index,
                absolute_amount_index,
                ocr_dates,
                available_ocr,
                amount_mode="absolute",
                max_abs_day_delta=0,
                merchant_key=merchant_key,
            )
            absolute_same_day_assignments = assign_candidates(
                processing_order,
                description,
                suggested_merchant,
                unresolved,
                absolute_same_day_candidates,
                "absolute_same_day",
            )
            available_ocr.difference_update(
                assignment.ocr.row_image
                for assignment in absolute_same_day_assignments
            )

            assigned_ids.update(
                assignment.transaction.transaction_id
                for assignment in absolute_same_day_assignments
            )
            unresolved = [
                transaction
                for transaction in evidence_transactions
                if transaction.transaction_id not in assigned_ids
            ]
            absolute_window_candidates = build_candidates(
                unresolved,
                amount_index,
                absolute_amount_index,
                ocr_dates,
                available_ocr,
                amount_mode="absolute",
                max_abs_day_delta=secondary_day_window,
                merchant_key=merchant_key,
                include_same_day=False,
            )
            absolute_window_assignments = assign_candidates(
                processing_order,
                description,
                suggested_merchant,
                unresolved,
                absolute_window_candidates,
                "absolute_window",
            )
            available_ocr.difference_update(
                assignment.ocr.row_image
                for assignment in absolute_window_assignments
            )

        group_assignments = (
            signed_same_day_assignments
            + absolute_same_day_assignments
            + absolute_window_assignments
        )
        assignments.extend(group_assignments)
        assigned_transaction_ids = {
            assignment.transaction.transaction_id
            for assignment in group_assignments
        }

        for transaction in group:
            if transaction.transaction_id in assigned_transaction_ids:
                continue
            remaining: dict[str, Candidate] = {}
            if transaction.amount <= 0:
                unresolved_candidates[transaction.transaction_id] = []
                continue
            for candidate in build_candidates(
                [transaction],
                amount_index,
                absolute_amount_index,
                ocr_dates,
                available_ocr,
                amount_mode="opposite",
                max_abs_day_delta=0,
            ).get(transaction.transaction_id, []):
                remaining[candidate.ocr.row_image] = candidate
            for candidate in build_candidates(
                [transaction],
                amount_index,
                absolute_amount_index,
                ocr_dates,
                available_ocr,
                amount_mode="absolute",
                max_abs_day_delta=0,
            ).get(transaction.transaction_id, []):
                remaining[candidate.ocr.row_image] = candidate
            if merchant_key is not None:
                for candidate in build_candidates(
                    [transaction],
                    amount_index,
                    absolute_amount_index,
                    ocr_dates,
                    available_ocr,
                    amount_mode="absolute",
                    max_abs_day_delta=secondary_day_window,
                    merchant_key=merchant_key,
                    include_same_day=False,
                ).get(transaction.transaction_id, []):
                    remaining[candidate.ocr.row_image] = candidate
            unresolved_candidates[transaction.transaction_id] = sorted(
                remaining.values(), key=candidate_sort_key
            )

        consumed = {assignment.ocr.row_image for assignment in group_assignments}
        has_mapping = suggested_merchant is not None
        mapping_only_transactions = [
            transaction
            for transaction in group
            if has_mapping
            and transaction.transaction_id not in assigned_transaction_ids
        ]
        mapping_only_reason_counts = Counter(
            mapping_only_reason(
                transaction,
                unresolved_candidates.get(transaction.transaction_id, []),
                coverage_start,
                coverage_end,
            )
            for transaction in mapping_only_transactions
        )
        results.append(
            GroupResult(
                processing_order=processing_order,
                description=description,
                transaction_count=len(group),
                covered_transaction_count=len(covered),
                eligible_evidence_transaction_count=len(evidence_transactions),
                seed_transaction_count=sum(chosen_seeds.values()),
                seed_merchants=seed_merchants,
                seed_stage=seed_stage,
                mapping_confidence=mapping_confidence,
                suggested_merchant=suggested_merchant,
                status=status,
                ocr_evidence_assignment_count=len(group_assignments),
                signed_same_day_assignment_count=len(signed_same_day_assignments),
                absolute_same_day_assignment_count=len(absolute_same_day_assignments),
                absolute_window_assignment_count=len(absolute_window_assignments),
                mapped_transaction_count=(len(group) if has_mapping else 0),
                mapping_only_transaction_count=len(mapping_only_transactions),
                unresolved_mapping_transaction_count=(0 if has_mapping else len(group)),
                refund_mapping_only_count=mapping_only_reason_counts[
                    "refund_not_in_ocr_scope"
                ],
                coverage_mapping_only_count=mapping_only_reason_counts[
                    "ocr_not_covered"
                ],
                no_evidence_mapping_only_count=mapping_only_reason_counts[
                    "no_ocr_evidence"
                ],
                candidate_not_assigned_mapping_only_count=mapping_only_reason_counts[
                    "ocr_candidate_not_assigned"
                ],
                ocr_available_before=available_before,
                ocr_consumed=len(consumed),
                ocr_available_after=len(available_ocr),
            )
        )

    return (
        results,
        assignments,
        unresolved_candidates,
        available_ocr,
        coverage_start,
        coverage_end,
    )

def write_group_results(path: Path, results: list[GroupResult]) -> None:
    fields = (
        "processing_order",
        "description",
        "transaction_count",
        "covered_transaction_count",
        "eligible_evidence_transaction_count",
        "seed_transaction_count",
        "seed_merchants",
        "seed_stage",
        "mapping_confidence",
        "suggested_merchant",
        "status",
        "ocr_evidence_assignment_count",
        "signed_same_day_assignment_count",
        "absolute_same_day_assignment_count",
        "absolute_window_assignment_count",
        "mapped_transaction_count",
        "mapping_only_transaction_count",
        "unresolved_mapping_transaction_count",
        "refund_mapping_only_count",
        "coverage_mapping_only_count",
        "no_evidence_mapping_only_count",
        "candidate_not_assigned_mapping_only_count",
        "ocr_available_before",
        "ocr_consumed",
        "ocr_available_after",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "processing_order": result.processing_order,
                    "description": result.description,
                    "transaction_count": result.transaction_count,
                    "covered_transaction_count": result.covered_transaction_count,
                    "eligible_evidence_transaction_count": result.eligible_evidence_transaction_count,
                    "seed_transaction_count": result.seed_transaction_count,
                    "seed_merchants": " | ".join(
                        f"{merchant}: {count}"
                        for merchant, count in result.seed_merchants
                    ),
                    "seed_stage": result.seed_stage or "",
                    "mapping_confidence": result.mapping_confidence or "",
                    "suggested_merchant": result.suggested_merchant or "",
                    "status": result.status,
                    "ocr_evidence_assignment_count": result.ocr_evidence_assignment_count,
                    "signed_same_day_assignment_count": result.signed_same_day_assignment_count,
                    "absolute_same_day_assignment_count": result.absolute_same_day_assignment_count,
                    "absolute_window_assignment_count": result.absolute_window_assignment_count,
                    "mapped_transaction_count": result.mapped_transaction_count,
                    "mapping_only_transaction_count": result.mapping_only_transaction_count,
                    "unresolved_mapping_transaction_count": result.unresolved_mapping_transaction_count,
                    "refund_mapping_only_count": result.refund_mapping_only_count,
                    "coverage_mapping_only_count": result.coverage_mapping_only_count,
                    "no_evidence_mapping_only_count": result.no_evidence_mapping_only_count,
                    "candidate_not_assigned_mapping_only_count": result.candidate_not_assigned_mapping_only_count,
                    "ocr_available_before": result.ocr_available_before,
                    "ocr_consumed": result.ocr_consumed,
                    "ocr_available_after": result.ocr_available_after,
                }
            )

def match_confidence(match_stage: str) -> str:
    return {
        "signed_same_day": "high",
        "absolute_same_day": "medium",
        "absolute_window": "low",
    }[match_stage]


def write_assignments(path: Path, assignments: list[Assignment]) -> None:
    fields = (
        "processing_order",
        "description",
        "suggested_merchant",
        "transaction_id",
        "transaction_kind",
        "transaction_date",
        "bank_amount",
        "match_stage",
        "match_confidence",
        "day_delta",
        "source_email",
        "source_index",
        "row_image",
        "ocr_amount",
        "merchant_text",
        "occurred_on",
        "occurred_at",
        "occurred_at_precision",
        "issues",
        "notes",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for assignment in assignments:
            writer.writerow(
                {
                    "processing_order": assignment.processing_order,
                    "description": assignment.description,
                    "suggested_merchant": assignment.suggested_merchant,
                    "transaction_id": assignment.transaction.transaction_id,
                    "transaction_kind": transaction_kind(assignment.transaction),
                    "transaction_date": assignment.transaction.transaction_date.isoformat(),
                    "bank_amount": assignment.transaction.amount,
                    "match_stage": assignment.match_stage,
                    "match_confidence": match_confidence(assignment.match_stage),
                    "day_delta": assignment.day_delta,
                    "source_email": assignment.transaction.source_email,
                    "source_index": assignment.transaction.source_index,
                    "row_image": assignment.ocr.row_image,
                    "ocr_amount": assignment.ocr.amount,
                    "merchant_text": assignment.ocr.merchant_text,
                    "occurred_on": assignment.occurred_on.isoformat(),
                    "occurred_at": assignment.ocr.occurred_at or "",
                    "occurred_at_precision": assignment.ocr.precision or "",
                    "issues": ", ".join(assignment.ocr.issues),
                    "notes": ", ".join(assignment.ocr.notes),
                }
            )

def is_ocr_covered(
    transaction: Transaction,
    coverage_start: date | None,
    coverage_end: date | None,
) -> bool:
    return (
        coverage_start is not None
        and coverage_end is not None
        and coverage_start <= transaction.transaction_date <= coverage_end
    )


def mapping_only_reason(
    transaction: Transaction,
    candidates: list[Candidate],
    coverage_start: date | None,
    coverage_end: date | None,
) -> str:
    if not is_ocr_covered(transaction, coverage_start, coverage_end):
        return "ocr_not_covered"
    if transaction.amount < 0:
        return "refund_not_in_ocr_scope"
    if candidates:
        return "ocr_candidate_not_assigned"
    return "no_ocr_evidence"


def unresolved_reason(
    transaction: Transaction,
    candidates: list[Candidate],
    coverage_start: date | None,
    coverage_end: date | None,
) -> str:
    if not is_ocr_covered(transaction, coverage_start, coverage_end):
        return "ocr_not_covered"
    if transaction.amount < 0:
        return "refund_without_description_mapping"
    if any(candidate.day_delta == 0 for candidate in candidates):
        return "remaining_same_day_candidates"
    if candidates:
        return "remaining_secondary_candidates"
    return "no_ocr_evidence"


def candidate_details(candidates: list[Candidate]) -> str:
    return " / ".join(
        f"{candidate.ocr.merchant_text} @ {candidate.occurred_on.isoformat()} "
        f"(delta={candidate.day_delta}, ocr_amount={candidate.ocr.amount}, "
        f"row={candidate.ocr.row_image})"
        for candidate in candidates
    )


def write_transaction_mapping_status(
    path: Path,
    transactions: list[Transaction],
    results: list[GroupResult],
    assignments: list[Assignment],
    candidates: dict[str, list[Candidate]],
    coverage_start: date | None,
    coverage_end: date | None,
) -> None:
    results_by_description = {result.description: result for result in results}
    assignments_by_transaction = {
        assignment.transaction.transaction_id: assignment
        for assignment in assignments
    }
    fields = (
        "transaction_id",
        "transaction_kind",
        "transaction_date",
        "bank_amount",
        "description",
        "suggested_merchant",
        "mapping_status",
        "resolution_reason",
        "match_stage",
        "match_confidence",
        "ocr_row",
        "ocr_amount",
        "ocr_merchant",
        "candidate_count",
        "candidate_details",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for transaction in sorted(transactions, key=transaction_sort_key):
            result = results_by_description[transaction.description]
            assignment = assignments_by_transaction.get(transaction.transaction_id)
            transaction_candidates = candidates.get(transaction.transaction_id, [])
            if assignment is not None:
                mapping_status = "ocr_evidence"
                resolution_reason = assignment.match_stage
            elif result.suggested_merchant is not None:
                mapping_status = "description_mapping_only"
                resolution_reason = mapping_only_reason(
                    transaction,
                    transaction_candidates,
                    coverage_start,
                    coverage_end,
                )
            else:
                mapping_status = "unresolved"
                resolution_reason = unresolved_reason(
                    transaction,
                    transaction_candidates,
                    coverage_start,
                    coverage_end,
                )
            writer.writerow(
                {
                    "transaction_id": transaction.transaction_id,
                    "transaction_kind": transaction_kind(transaction),
                    "transaction_date": transaction.transaction_date.isoformat(),
                    "bank_amount": transaction.amount,
                    "description": transaction.description,
                    "suggested_merchant": result.suggested_merchant or "",
                    "mapping_status": mapping_status,
                    "resolution_reason": resolution_reason,
                    "match_stage": assignment.match_stage if assignment else "",
                    "match_confidence": (
                        match_confidence(assignment.match_stage)
                        if assignment
                        else ""
                    ),
                    "ocr_row": assignment.ocr.row_image if assignment else "",
                    "ocr_amount": (
                        assignment.ocr.amount
                        if assignment and assignment.ocr.amount is not None
                        else ""
                    ),
                    "ocr_merchant": assignment.ocr.merchant_text if assignment else "",
                    "candidate_count": len(transaction_candidates),
                    "candidate_details": candidate_details(transaction_candidates),
                }
            )


def write_unresolved(
    path: Path,
    transactions: list[Transaction],
    results: list[GroupResult],
    candidates: dict[str, list[Candidate]],
    coverage_start: date | None,
    coverage_end: date | None,
) -> None:
    results_by_description = {result.description: result for result in results}
    fields = (
        "transaction_id",
        "transaction_kind",
        "transaction_date",
        "bank_amount",
        "description",
        "reason",
        "candidate_count",
        "candidate_merchants",
        "candidate_dates",
        "candidate_day_deltas",
        "candidate_rows",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for transaction in sorted(transactions, key=transaction_sort_key):
            result = results_by_description[transaction.description]
            if result.suggested_merchant is not None:
                continue
            transaction_candidates = candidates.get(transaction.transaction_id, [])
            writer.writerow(
                {
                    "transaction_id": transaction.transaction_id,
                    "transaction_kind": transaction_kind(transaction),
                    "transaction_date": transaction.transaction_date.isoformat(),
                    "bank_amount": transaction.amount,
                    "description": transaction.description,
                    "reason": unresolved_reason(
                        transaction,
                        transaction_candidates,
                        coverage_start,
                        coverage_end,
                    ),
                    "candidate_count": len(transaction_candidates),
                    "candidate_merchants": " | ".join(
                        sorted(
                            {
                                candidate.ocr.merchant_text
                                for candidate in transaction_candidates
                            }
                        )
                    ),
                    "candidate_dates": " | ".join(
                        candidate.occurred_on.isoformat()
                        for candidate in transaction_candidates
                    ),
                    "candidate_day_deltas": " | ".join(
                        str(candidate.day_delta)
                        for candidate in transaction_candidates
                    ),
                    "candidate_rows": " | ".join(
                        candidate.ocr.row_image
                        for candidate in transaction_candidates
                    ),
                }
            )

def write_unused_ocr(
    path: Path,
    ocr_rows: list[OCRRow],
    available_ocr: set[str],
) -> None:
    fields = (
        "row_image",
        "ocr_amount",
        "merchant_text",
        "occurred_on",
        "occurred_at",
        "occurred_at_precision",
        "issues",
        "notes",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(
            (row for row in ocr_rows if row.row_image in available_ocr),
            key=lambda row: (row.source_stem, row.row_index, row.row_image),
        ):
            occurred_on = infer_ocr_date(row)
            writer.writerow(
                {
                    "row_image": row.row_image,
                    "ocr_amount": row.amount if row.amount is not None else "",
                    "merchant_text": row.merchant_text,
                    "occurred_on": occurred_on.isoformat() if occurred_on else "",
                    "occurred_at": row.occurred_at or "",
                    "occurred_at_precision": row.precision or "",
                    "issues": ", ".join(row.issues),
                    "notes": ", ".join(row.notes),
                }
            )


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def review_field(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def build_review_markdown(
    results: list[GroupResult],
    assignments: list[Assignment],
    transactions: list[Transaction],
    unresolved_candidates: dict[str, list[Candidate]],
    coverage_start: date | None,
    coverage_end: date | None,
    secondary_day_window: int,
) -> str:
    transactions_by_description: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        transactions_by_description[transaction.description].append(transaction)

    assignments_by_description: dict[str, list[Assignment]] = defaultdict(list)
    assignments_by_transaction: dict[str, Assignment] = {}
    for assignment in assignments:
        assignments_by_description[assignment.description].append(assignment)
        assignments_by_transaction[assignment.transaction.transaction_id] = assignment

    lines = [
        "# Merchant Mapping Review",
        "",
        "每个二级标题对应一个原始 `description`。请逐项浏览；正常建议无需修改。",
        "",
        "编辑规则：",
        "",
        "- `action: accept`：接受该 Mapping；可以修改 `merchant_name` 和 `category`。",
        "- `action: reject`：明确拒绝，本轮不写入正式 Mapping。",
        "- `action: unresolved`：证据不足，保留到后续处理。",
        "- 有自动建议的项目默认 `accept`；没有可靠建议的项目默认 `unresolved`。",
        "- `description` 是正式 Mapping 的查找键，不要修改。",
        "- OCR 只用于提供 `description -> merchant_name` 的证据，不是第二份交易账本。",
        "- 退款未包含在当前 App 导入范围内，因此不参与 OCR 证据匹配。",
        "- 第一层优先使用银行与 OCR 金额符号相反且同日的正金额账单交易。",
        "- 第二层在同日内忽略金额符号，只比较绝对金额。",
        f"- 第三层仅在已确定 merchant_name 后，使用同 merchant、相同绝对金额和前后 {secondary_day_window} 天窗口。",
        "- description 一旦建立 Mapping，该 description 下所有退款、OCR 覆盖外交易和无 OCR 证据交易都继承该 merchant_name。",
        "- 金额统计始终使用原始银行账单。",
        "- 本审核文件完整列出所有交易，不做条数截断。",
        "- 本文件重新生成时会被覆盖；它当前只用于人工审核，不会自动更新正式 Mapping。",
        "",
        f"OCR 日期覆盖：{coverage_start or 'unknown'} 至 {coverage_end or 'unknown'}。",
        "",
    ]

    for result in results:
        group_transactions = sorted(
            transactions_by_description[result.description],
            key=transaction_sort_key,
        )
        group_assignments = sorted(
            assignments_by_description.get(result.description, []),
            key=lambda assignment: (
                transaction_sort_key(assignment.transaction),
                assignment.match_stage,
            ),
        )
        non_evidence_transactions = [
            transaction
            for transaction in group_transactions
            if transaction.transaction_id not in assignments_by_transaction
        ]
        action = "accept" if result.suggested_merchant else "unresolved"
        merchant_name = result.suggested_merchant or ""
        seed_summary = "; ".join(
            f"{merchant}: {count}" for merchant, count in result.seed_merchants
        ) or "无"

        lines.extend(
            [
                f"## {result.processing_order:04d} · {review_field(result.description)}",
                "",
                "```mapping-review",
                f"description: {review_field(result.description)}",
                f"action: {action}",
                f"merchant_name: {review_field(merchant_name)}",
                "category: 未分类",
                "```",
                "",
                f"- 自动状态：`{result.status}`",
                f"- Mapping 置信度：`{result.mapping_confidence or 'none'}`；种子层级：`{result.seed_stage or 'none'}`",
                f"- 交易总数：{result.transaction_count} 笔；OCR 日期覆盖内 {result.covered_transaction_count} 笔；可参与 OCR 证据匹配的正金额交易 {result.eligible_evidence_transaction_count} 笔",
                f"- OCR 直接证据：{result.ocr_evidence_assignment_count} 笔；建议 Mapping 覆盖：{result.mapped_transaction_count} 笔；仅靠 description Mapping 解析：{result.mapping_only_transaction_count} 笔；仍未获得商户 Mapping：{result.unresolved_mapping_transaction_count} 笔",
                f"- OCR 证据构成：符号相反同日 {result.signed_same_day_assignment_count}；绝对金额同日 {result.absolute_same_day_assignment_count}；绝对金额窗口 {result.absolute_window_assignment_count}",
                f"- Mapping-only 构成：退款不在 OCR 范围 {result.refund_mapping_only_count}；OCR 日期未覆盖 {result.coverage_mapping_only_count}；范围内无 OCR 证据 {result.no_evidence_mapping_only_count}；有候选但未分配 {result.candidate_not_assigned_mapping_only_count}",
                f"- 本组新增消耗 OCR：{result.ocr_consumed} 条",
                f"- 种子证据：{seed_summary}",
                "",
            ]
        )

        if group_assignments:
            lines.extend(
                [
                    f"### OCR 直接证据（共 {len(group_assignments)} 笔）",
                    "",
                    "| Bank date | Type | Bank amount | Match | Confidence | Day delta | Transaction | OCR merchant | OCR amount | OCR time | Row | Issues |",
                    "| --- | --- | ---: | --- | --- | ---: | --- | --- | ---: | --- | --- | --- |",
                ]
            )
            for assignment in group_assignments:
                lines.append(
                    f"| {assignment.transaction.transaction_date.isoformat()} "
                    f"| {transaction_kind(assignment.transaction)} "
                    f"| {assignment.transaction.amount} "
                    f"| `{assignment.match_stage}` "
                    f"| `{match_confidence(assignment.match_stage)}` "
                    f"| {assignment.day_delta} "
                    f"| `{assignment.transaction.transaction_id}` "
                    f"| {markdown_cell(assignment.ocr.merchant_text)} "
                    f"| {assignment.ocr.amount if assignment.ocr.amount is not None else ''} "
                    f"| {markdown_cell(assignment.ocr.occurred_at or assignment.occurred_on.isoformat())} "
                    f"| `{assignment.ocr.row_image}` "
                    f"| {markdown_cell(', '.join(assignment.ocr.issues))} |"
                )
            lines.append("")
        else:
            lines.extend(["### OCR 直接证据", "", "无。", ""])

        if non_evidence_transactions:
            rows: list[tuple[Transaction, list[Candidate], str]] = []
            reason_counts: Counter[str] = Counter()
            for transaction in non_evidence_transactions:
                transaction_candidates = unresolved_candidates.get(
                    transaction.transaction_id, []
                )
                if result.suggested_merchant is not None:
                    reason = mapping_only_reason(
                        transaction,
                        transaction_candidates,
                        coverage_start,
                        coverage_end,
                    )
                else:
                    reason = unresolved_reason(
                        transaction,
                        transaction_candidates,
                        coverage_start,
                        coverage_end,
                    )
                reason_counts[reason] += 1
                rows.append((transaction, transaction_candidates, reason))

            reason_summary = "; ".join(
                f"{reason}: {count}"
                for reason, count in sorted(reason_counts.items())
            )
            heading = (
                "由 description Mapping 解析的其他交易"
                if result.suggested_merchant is not None
                else "仍未获得商户 Mapping 的交易"
            )
            lines.extend(
                [
                    f"### {heading}（共 {len(rows)} 笔）",
                    "",
                    f"原因汇总：{reason_summary}",
                    "",
                    "| Date | Type | Bank amount | Transaction | Resolution | Merchant | Candidate details |",
                    "| --- | --- | ---: | --- | --- | --- | --- |",
                ]
            )
            for transaction, transaction_candidates, reason in rows:
                lines.append(
                    f"| {transaction.transaction_date.isoformat()} "
                    f"| {transaction_kind(transaction)} "
                    f"| {transaction.amount} "
                    f"| `{transaction.transaction_id}` "
                    f"| `{reason}` "
                    f"| {markdown_cell(result.suggested_merchant or '')} "
                    f"| {markdown_cell(candidate_details(transaction_candidates))} |"
                )
            lines.append("")
        elif result.suggested_merchant is not None:
            lines.extend(
                ["### 由 description Mapping 解析的其他交易", "", "无。", ""]
            )
        else:
            lines.extend(
                ["### 仍未获得商户 Mapping 的交易", "", "无。", ""]
            )

        lines.extend(["---", ""])

    return "\n".join(lines)

def build_summary(
    results: list[GroupResult],
    assignments: list[Assignment],
    transactions: list[Transaction],
    ocr_rows: list[OCRRow],
    available_ocr: set[str],
    coverage_start: date | None,
    coverage_end: date | None,
    example_limit: int,
    secondary_day_window: int,
) -> str:
    statuses = Counter(result.status for result in results)
    assignment_stages = Counter(assignment.match_stage for assignment in assignments)
    mapped_count = sum(result.suggested_merchant is not None for result in results)
    mapped_transactions = sum(result.mapped_transaction_count for result in results)
    mapping_only_transactions = sum(
        result.mapping_only_transaction_count for result in results
    )
    unresolved_mapping_transactions = sum(
        result.unresolved_mapping_transaction_count for result in results
    )
    lines = [
        "# Description Matching Inspection",
        "",
        "This is a read-only greedy simulation. It does not update formal Mapping data or OCR input.",
        "",
        "## Rules Used",
        "",
        "- Group bank transactions by exact `description`.",
        "- Process groups by transaction count descending.",
        "- OCR is evidence for `description -> merchant_name`, not a second transaction ledger.",
        "- Refunds were excluded from the current App export and do not compete for OCR rows.",
        "- Tier 1: positive bank amount, opposite-signed OCR amount, and exact date.",
        "- Tier 2: positive bank amount, equal absolute OCR amount, and exact date.",
        f"- Tier 3: established merchant, equal absolute amount, and a {secondary_day_window}-day date window.",
        "- Tier 3 cannot create a merchant hypothesis by itself.",
        "- Consume each assigned OCR row once; unmatched or duplicate OCR rows may remain unused.",
        "- Once a description has a merchant hypothesis, every transaction with that exact description inherits it.",
        "- Financial totals remain based on bank transactions.",
        "- Merchant variants are compared by exact normalized text only in this version.",
        "- The review Markdown includes every transaction.",
        "",
        "## Summary",
        "",
        f"- Bank transactions: {len(transactions)}",
        f"- Exact descriptions: {len(results)}",
        f"- OCR rows: {len(ocr_rows)}",
        f"- OCR inferred date coverage: {coverage_start or 'unknown'} to {coverage_end or 'unknown'}",
        f"- Suggested description mappings: {mapped_count}",
        f"- High-confidence mappings: {sum(result.mapping_confidence == 'high' for result in results)}",
        f"- Medium-confidence mappings: {sum(result.mapping_confidence == 'medium' for result in results)}",
        f"- Transactions with suggested merchant Mapping: {mapped_transactions}",
        f"- Transactions with direct OCR evidence: {len(assignments)}",
        f"- Transactions resolved by description Mapping only: {mapping_only_transactions}",
        f"- Transactions still without merchant Mapping: {unresolved_mapping_transactions}",
        f"- Mapping-only refunds excluded from OCR scope: {sum(result.refund_mapping_only_count for result in results)}",
        f"- Mapping-only transactions outside OCR date coverage: {sum(result.coverage_mapping_only_count for result in results)}",
        f"- Mapping-only in-range transactions without OCR evidence: {sum(result.no_evidence_mapping_only_count for result in results)}",
        f"- Mapping-only transactions with unassigned OCR candidates: {sum(result.candidate_not_assigned_mapping_only_count for result in results)}",
        f"- Conflicting signed same-day seed groups: {statuses['conflicting_signed_same_day_seeds']}",
        f"- Conflicting absolute same-day seed groups: {statuses['conflicting_absolute_same_day_seeds']}",
        f"- Ambiguous groups without seed: {statuses['ambiguous_without_seed']}",
        f"- Groups without same-day candidates: {statuses['no_same_day_candidate']}",
        f"- Groups with no eligible positive purchase evidence: {statuses['no_eligible_purchase_evidence']}",
        f"- Groups outside OCR coverage: {statuses['ocr_not_covered']}",
        f"- Tier 1 signed same-day assignments: {assignment_stages['signed_same_day']}",
        f"- Tier 2 absolute same-day assignments: {assignment_stages['absolute_same_day']}",
        f"- Tier 3 absolute-window assignments: {assignment_stages['absolute_window']}",
        f"- OCR rows consumed: {len({assignment.ocr.row_image for assignment in assignments})}",
        f"- OCR rows left unused: {len(available_ocr)}",
        "",
        "## Highest-frequency Description Groups",
        "",
        "| Order | Transactions | OCR evidence eligible | Description | Status | Confidence | Suggested merchant | Seeds | OCR evidence | Mapping-only | Mapped total | Unresolved Mapping |",
        "| ---: | ---: | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for result in results[:example_limit]:
        seeds = "; ".join(
            f"{merchant}: {count}" for merchant, count in result.seed_merchants
        )
        lines.append(
            f"| {result.processing_order} "
            f"| {result.transaction_count} "
            f"| {result.eligible_evidence_transaction_count} "
            f"| {markdown_cell(result.description)} "
            f"| {result.status} "
            f"| {result.mapping_confidence or ''} "
            f"| {markdown_cell(result.suggested_merchant or '')} "
            f"| {markdown_cell(seeds)} "
            f"| {result.ocr_evidence_assignment_count} "
            f"| {result.mapping_only_transaction_count} "
            f"| {result.mapped_transaction_count} "
            f"| {result.unresolved_mapping_transaction_count} |"
        )

    conflict_groups = [
        result
        for result in results
        if result.status.startswith("conflicting_")
    ]
    lines.extend(
        [
            "",
            "## Conflicting Seed Merchant Groups",
            "",
            "| Order | Transactions | Description | Status | Seed merchants |",
            "| ---: | ---: | --- | --- | --- |",
        ]
    )
    for result in conflict_groups[:example_limit]:
        seeds = "; ".join(
            f"{merchant}: {count}" for merchant, count in result.seed_merchants
        )
        lines.append(
            f"| {result.processing_order} "
            f"| {result.transaction_count} "
            f"| {markdown_cell(result.description)} "
            f"| {result.status} "
            f"| {markdown_cell(seeds)} |"
        )
    if not conflict_groups:
        lines.append("| | | None | | |")

    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `description_groups.csv`: one row per exact description in processing order.",
            "- `description_assignments.csv`: direct transaction/OCR evidence assignments with tier and confidence.",
            "- `transaction_mapping_status.csv`: every bank transaction and whether it has OCR evidence, inherits a description Mapping, or remains unresolved.",
            "- `unresolved_transactions.csv`: only transactions whose descriptions still have no suggested merchant Mapping.",
            "- `unused_ocr.csv`: OCR rows not consumed by the simulation.",
            "- `mapping_review.md`: editable description-level review document with every transaction.",
            "",
        ]
    )
    return "\n".join(lines)

def main() -> None:
    args = parse_args()
    if args.example_limit <= 0:
        raise ValueError("example-limit must be greater than zero")
    if args.secondary_day_window < 0:
        raise ValueError("secondary-day-window must be zero or greater")

    logger.info("Transactions: {}", args.transactions)
    logger.info("OCR results: {}", args.ocr)
    logger.info("Secondary day window: {}", args.secondary_day_window)
    transactions = read_transactions(args.transactions)
    ocr_rows = read_ocr_rows(args.ocr)
    (
        results,
        assignments,
        unresolved_candidates,
        available_ocr,
        coverage_start,
        coverage_end,
    ) = process_groups(
        transactions,
        ocr_rows,
        args.secondary_day_window,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_group_results(args.output_dir / "description_groups.csv", results)
    write_assignments(args.output_dir / "description_assignments.csv", assignments)
    write_transaction_mapping_status(
        args.output_dir / "transaction_mapping_status.csv",
        transactions,
        results,
        assignments,
        unresolved_candidates,
        coverage_start,
        coverage_end,
    )
    write_unresolved(
        args.output_dir / "unresolved_transactions.csv",
        transactions,
        results,
        unresolved_candidates,
        coverage_start,
        coverage_end,
    )
    write_unused_ocr(args.output_dir / "unused_ocr.csv", ocr_rows, available_ocr)
    review_path = args.output_dir / "mapping_review.md"
    review_path.write_text(
        build_review_markdown(
            results,
            assignments,
            transactions,
            unresolved_candidates,
            coverage_start,
            coverage_end,
            args.secondary_day_window,
        )
        + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "summary.md").write_text(
        build_summary(
            results,
            assignments,
            transactions,
            ocr_rows,
            available_ocr,
            coverage_start,
            coverage_end,
            args.example_limit,
            args.secondary_day_window,
        )
        + "\n",
        encoding="utf-8",
    )

    suggested_mapping_count = sum(
        result.suggested_merchant is not None for result in results
    )
    mapped_transaction_count = sum(
        result.mapped_transaction_count for result in results
    )
    unresolved_mapping_transaction_count = sum(
        result.unresolved_mapping_transaction_count for result in results
    )
    logger.info("Description groups: {}", len(results))
    logger.info("Suggested mappings: {}", suggested_mapping_count)
    logger.info("Direct OCR evidence assignments: {}", len(assignments))
    logger.info(
        "Transactions covered by suggested mappings: {}",
        mapped_transaction_count,
    )
    logger.info(
        "Transactions still without merchant mapping: {}",
        unresolved_mapping_transaction_count,
    )
    logger.info("Unused OCR rows: {}", len(available_ocr))
    logger.info("Summary: {}", args.output_dir / "summary.md")
    logger.info("Review: {}", review_path)

if __name__ == "__main__":
    main()
