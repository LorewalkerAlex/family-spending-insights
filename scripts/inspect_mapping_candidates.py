from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from loguru import logger

TRANSACTION_FIELDS = {
    "transaction_id",
    "transaction_date",
    "amount",
    "description",
    "source_email",
    "source_index",
}
OCR_FIELDS = {
    "row_image",
    "merchant_text",
    "amount",
    "occurred_at",
    "issues",
}
ROW_NAME_RE = re.compile(r"^(?P<source>.+)__(?P<index>\d+)$")


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    transaction_date: date
    amount: Decimal
    description: str
    source_email: str
    source_index: int


@dataclass(frozen=True)
class OCRRow:
    row_image: str
    source_stem: str
    row_index: int
    merchant_text: str
    merchant_score: float
    amount: Decimal | None
    occurred_at: str | None
    occurred_on: str | None
    precision: str | None
    time_fields_inferred: bool
    issues: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def month_day(self) -> tuple[int, int] | None:
        value = self.occurred_at or self.occurred_on
        if not value:
            return None
        parsed = datetime.strptime(f"2000-{value[:5]}", "%Y-%m-%d")
        return parsed.month, parsed.day


@dataclass(frozen=True)
class Pair:
    transaction: Transaction
    ocr: OCRRow
    relation: str
    day_delta: int | None
    text_similarity: float
    description_contains_merchant: bool
    merchant_contains_description: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect candidate relationships between bank transactions and App OCR "
            "without selecting final matches or updating Mapping data."
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
        default=Path("tmp/mapping_candidate_inspection"),
    )
    parser.add_argument("--example-limit", type=int, default=20)
    return parser.parse_args()


def parse_decimal(value: object, context: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal for {context}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"Non-finite decimal for {context}: {value!r}")
    return parsed


def parse_string_list(value: object, context: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"Expected a list of strings for {context}")
    return tuple(value)


def read_transactions(path: Path) -> list[Transaction]:
    if not path.is_file():
        raise FileNotFoundError(path)

    transactions: list[Transaction] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(TRANSACTION_FIELDS - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"Missing transaction fields in {path}: {missing}")

        for line_number, row in enumerate(reader, 2):
            transaction_id = row["transaction_id"].strip()
            if not transaction_id:
                raise ValueError(f"Empty transaction_id at {path}:{line_number}")
            if transaction_id in seen_ids:
                raise ValueError(f"Duplicate transaction_id at {path}:{line_number}: {transaction_id}")
            seen_ids.add(transaction_id)
            try:
                transaction_date = date.fromisoformat(row["transaction_date"].strip())
                source_index = int(row["source_index"])
            except ValueError as exc:
                raise ValueError(f"Invalid transaction row at {path}:{line_number}: {row}") from exc
            transactions.append(
                Transaction(
                    transaction_id=transaction_id,
                    transaction_date=transaction_date,
                    amount=parse_decimal(row["amount"], f"{path}:{line_number} amount"),
                    description=row["description"].strip(),
                    source_email=row["source_email"].strip(),
                    source_index=source_index,
                )
            )

    if not transactions:
        raise ValueError(f"No transactions found in {path}")
    return transactions


def parse_row_name(row_image: str) -> tuple[str, int]:
    match = ROW_NAME_RE.fullmatch(Path(row_image).stem)
    if match is None:
        return Path(row_image).stem, 0
    return match.group("source"), int(match.group("index"))


def normalize_ocr_time(
    raw: dict[str, Any], context: str
) -> tuple[str | None, str | None, str | None, bool]:
    occurred_at_value = raw.get("occurred_at")
    occurred_on_value = raw.get("occurred_on")
    precision_value = raw.get("occurred_at_precision")

    occurred_at = (
        str(occurred_at_value).strip() if occurred_at_value is not None else None
    ) or None
    occurred_on = (
        str(occurred_on_value).strip() if occurred_on_value is not None else None
    ) or None
    precision = str(precision_value).strip() if precision_value is not None else None

    has_explicit_fields = (
        "occurred_on" in raw or "occurred_at_precision" in raw
    )
    if precision == "minute":
        if occurred_at is None:
            raise ValueError(f"Missing minute OCR time for {context}")
        try:
            datetime.strptime(f"2000-{occurred_at}", "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError(f"Invalid minute OCR time for {context}: {occurred_at!r}") from exc
        return occurred_at, occurred_on or occurred_at[:5], precision, False

    if precision == "date":
        date_value = occurred_on or occurred_at
        if date_value is None:
            raise ValueError(f"Missing date-only OCR time for {context}")
        try:
            datetime.strptime(f"2000-{date_value}", "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Invalid date-only OCR time for {context}: {date_value!r}") from exc
        return None, date_value, precision, False

    if precision not in (None, ""):
        raise ValueError(f"Invalid OCR time precision for {context}: {precision!r}")

    if occurred_at is None and occurred_on is None:
        return None, None, None, not has_explicit_fields

    if occurred_at is not None:
        try:
            datetime.strptime(f"2000-{occurred_at}", "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                datetime.strptime(f"2000-{occurred_at}", "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError(f"Invalid legacy OCR time for {context}: {occurred_at!r}") from exc
            return None, occurred_at, "date", True
        return occurred_at, occurred_on or occurred_at[:5], "minute", True

    try:
        datetime.strptime(f"2000-{occurred_on}", "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid OCR date for {context}: {occurred_on!r}") from exc
    return None, occurred_on, "date", True


def read_ocr_rows(path: Path) -> list[OCRRow]:
    if not path.is_file():
        raise FileNotFoundError(path)

    rows: list[OCRRow] = []
    seen_images: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_number}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_number}")
            missing = sorted(OCR_FIELDS - raw.keys())
            if missing:
                raise ValueError(f"Missing OCR fields at {path}:{line_number}: {missing}")

            row_image = str(raw["row_image"]).strip()
            if not row_image:
                raise ValueError(f"Empty row_image at {path}:{line_number}")
            if row_image in seen_images:
                raise ValueError(f"Duplicate row_image at {path}:{line_number}: {row_image}")
            seen_images.add(row_image)
            occurred_at, occurred_on, precision, time_fields_inferred = normalize_ocr_time(
                raw, f"{path}:{line_number}"
            )
            source_stem, row_index = parse_row_name(row_image)
            amount = raw.get("amount")
            rows.append(
                OCRRow(
                    row_image=row_image,
                    source_stem=source_stem,
                    row_index=row_index,
                    merchant_text=str(raw.get("merchant_text") or "").strip(),
                    merchant_score=float(raw.get("merchant_score") or 0.0),
                    amount=(
                        parse_decimal(amount, f"{path}:{line_number} amount")
                        if amount is not None and str(amount).strip()
                        else None
                    ),
                    occurred_at=occurred_at,
                    occurred_on=occurred_on,
                    precision=precision,
                    time_fields_inferred=time_fields_inferred,
                    issues=parse_string_list(raw.get("issues"), f"{path}:{line_number} issues"),
                    notes=parse_string_list(raw.get("notes"), f"{path}:{line_number} notes"),
                )
            )

    if not rows:
        raise ValueError(f"No OCR rows found in {path}")
    return rows


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def compare_text(description: str, merchant_text: str) -> tuple[float, bool, bool]:
    description_key = normalize_text(description)
    merchant_key = normalize_text(merchant_text)
    if not description_key or not merchant_key:
        return 0.0, False, False
    return (
        SequenceMatcher(None, description_key, merchant_key).ratio(),
        merchant_key in description_key,
        description_key in merchant_key,
    )


def nearest_day_delta(transaction_date: date, month_day: tuple[int, int] | None) -> int | None:
    if month_day is None:
        return None
    month, day = month_day
    deltas: list[int] = []
    for year in range(transaction_date.year - 1, transaction_date.year + 2):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        deltas.append((candidate - transaction_date).days)
    return min(deltas, key=lambda value: (abs(value), value)) if deltas else None


def build_pairs(transactions: list[Transaction], ocr_rows: list[OCRRow]) -> list[Pair]:
    by_absolute_amount: dict[Decimal, list[OCRRow]] = defaultdict(list)
    for row in ocr_rows:
        if row.amount is not None:
            by_absolute_amount[abs(row.amount)].append(row)

    pairs: list[Pair] = []
    for transaction in transactions:
        for row in by_absolute_amount.get(abs(transaction.amount), []):
            similarity, description_contains, merchant_contains = compare_text(
                transaction.description, row.merchant_text
            )
            if transaction.amount == 0 and row.amount == 0:
                relation = "both_zero"
            elif transaction.amount == -row.amount:
                relation = "opposite_sign"
            else:
                relation = "same_sign"
            pairs.append(
                Pair(
                    transaction=transaction,
                    ocr=row,
                    relation=relation,
                    day_delta=nearest_day_delta(transaction.transaction_date, row.month_day),
                    text_similarity=similarity,
                    description_contains_merchant=description_contains,
                    merchant_contains_description=merchant_contains,
                )
            )
    return pairs


def pair_sort_key(pair: Pair) -> tuple[object, ...]:
    relation_order = {"opposite_sign": 0, "same_sign": 1, "both_zero": 2}
    return (
        pair.transaction.transaction_date,
        pair.transaction.transaction_id,
        relation_order[pair.relation],
        abs(pair.day_delta) if pair.day_delta is not None else 10**9,
        -pair.text_similarity,
        pair.ocr.source_stem,
        pair.ocr.row_index,
    )


def write_candidate_pairs(path: Path, pairs: list[Pair]) -> None:
    fields = (
        "transaction_id",
        "transaction_date",
        "bank_amount",
        "description",
        "source_email",
        "source_index",
        "row_image",
        "source_stem",
        "row_index",
        "ocr_amount",
        "amount_relation",
        "merchant_text",
        "merchant_score",
        "occurred_at",
        "occurred_on",
        "occurred_at_precision",
        "nearest_day_delta",
        "text_similarity",
        "description_contains_merchant",
        "merchant_contains_description",
        "issues",
        "notes",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for pair in pairs:
            writer.writerow(
                {
                    "transaction_id": pair.transaction.transaction_id,
                    "transaction_date": pair.transaction.transaction_date.isoformat(),
                    "bank_amount": pair.transaction.amount,
                    "description": pair.transaction.description,
                    "source_email": pair.transaction.source_email,
                    "source_index": pair.transaction.source_index,
                    "row_image": pair.ocr.row_image,
                    "source_stem": pair.ocr.source_stem,
                    "row_index": pair.ocr.row_index,
                    "ocr_amount": pair.ocr.amount,
                    "amount_relation": pair.relation,
                    "merchant_text": pair.ocr.merchant_text,
                    "merchant_score": f"{pair.ocr.merchant_score:.4f}",
                    "occurred_at": pair.ocr.occurred_at or "",
                    "occurred_on": pair.ocr.occurred_on or "",
                    "occurred_at_precision": pair.ocr.precision or "",
                    "nearest_day_delta": "" if pair.day_delta is None else pair.day_delta,
                    "text_similarity": f"{pair.text_similarity:.4f}",
                    "description_contains_merchant": pair.description_contains_merchant,
                    "merchant_contains_description": pair.merchant_contains_description,
                    "issues": ", ".join(pair.ocr.issues),
                    "notes": ", ".join(pair.ocr.notes),
                }
            )


def group_pairs(pairs: list[Pair]) -> dict[str, list[Pair]]:
    grouped: dict[str, list[Pair]] = defaultdict(list)
    for pair in pairs:
        grouped[pair.transaction.transaction_id].append(pair)
    return grouped


def write_transaction_diagnostics(
    path: Path, transactions: list[Transaction], grouped: dict[str, list[Pair]]
) -> None:
    fields = (
        "transaction_id",
        "transaction_date",
        "amount",
        "description",
        "source_email",
        "source_index",
        "opposite_sign_candidates",
        "same_sign_candidates",
        "absolute_amount_candidates",
        "opposite_same_day_candidates",
        "opposite_within_1_day_candidates",
        "opposite_within_3_days_candidates",
        "opposite_within_7_days_candidates",
        "opposite_unknown_date_candidates",
        "opposite_distinct_merchants",
        "best_opposite_text_similarity",
        "best_opposite_row_image",
        "best_opposite_merchant_text",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for transaction in transactions:
            pairs = grouped.get(transaction.transaction_id, [])
            opposite = [pair for pair in pairs if pair.relation == "opposite_sign"]
            same = [pair for pair in pairs if pair.relation == "same_sign"]
            best = max(opposite, key=lambda pair: pair.text_similarity, default=None)
            writer.writerow(
                {
                    "transaction_id": transaction.transaction_id,
                    "transaction_date": transaction.transaction_date.isoformat(),
                    "amount": transaction.amount,
                    "description": transaction.description,
                    "source_email": transaction.source_email,
                    "source_index": transaction.source_index,
                    "opposite_sign_candidates": len(opposite),
                    "same_sign_candidates": len(same),
                    "absolute_amount_candidates": len(pairs),
                    "opposite_same_day_candidates": sum(pair.day_delta == 0 for pair in opposite),
                    "opposite_within_1_day_candidates": sum(
                        pair.day_delta is not None and abs(pair.day_delta) <= 1 for pair in opposite
                    ),
                    "opposite_within_3_days_candidates": sum(
                        pair.day_delta is not None and abs(pair.day_delta) <= 3 for pair in opposite
                    ),
                    "opposite_within_7_days_candidates": sum(
                        pair.day_delta is not None and abs(pair.day_delta) <= 7 for pair in opposite
                    ),
                    "opposite_unknown_date_candidates": sum(
                        pair.day_delta is None for pair in opposite
                    ),
                    "opposite_distinct_merchants": len(
                        {normalize_text(pair.ocr.merchant_text) for pair in opposite if pair.ocr.merchant_text}
                    ),
                    "best_opposite_text_similarity": (
                        f"{best.text_similarity:.4f}" if best else ""
                    ),
                    "best_opposite_row_image": best.ocr.row_image if best else "",
                    "best_opposite_merchant_text": best.ocr.merchant_text if best else "",
                }
            )


def format_distribution(values: list[int]) -> str:
    counts = Counter(values)
    return ", ".join(
        f"{candidate_count}: {transaction_count}"
        for candidate_count, transaction_count in sorted(counts.items())
    )


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_summary(
    transactions: list[Transaction],
    ocr_rows: list[OCRRow],
    pairs: list[Pair],
    example_limit: int,
) -> str:
    grouped = group_pairs(pairs)
    opposite_counts = [
        sum(pair.relation == "opposite_sign" for pair in grouped.get(item.transaction_id, []))
        for item in transactions
    ]
    same_counts = [
        sum(pair.relation == "same_sign" for pair in grouped.get(item.transaction_id, []))
        for item in transactions
    ]
    opposite_pairs = [pair for pair in pairs if pair.relation == "opposite_sign"]
    known_dates = [pair for pair in opposite_pairs if pair.day_delta is not None]
    relations = Counter(pair.relation for pair in pairs)
    issues = Counter(issue for row in ocr_rows for issue in row.issues)

    lines = [
        "# Mapping Candidate Inspection",
        "",
        "This is a read-only diagnostic. It does not select matches or update Mapping data.",
        "",
        "## Summary",
        "",
        f"- Bank transactions: {len(transactions)}",
        f"- OCR rows: {len(ocr_rows)}",
        f"- OCR rows with parsed amount: {sum(row.amount is not None for row in ocr_rows)}",
        f"- OCR rows without parsed amount: {sum(row.amount is None for row in ocr_rows)}",
        f"- OCR rows with explicit time fields: {sum(not row.time_fields_inferred for row in ocr_rows)}",
        f"- OCR rows using legacy time inference: {sum(row.time_fields_inferred for row in ocr_rows)}",
        f"- Absolute-amount candidate pairs: {len(pairs)}",
        f"- Opposite-sign pairs: {relations['opposite_sign']}",
        f"- Same-sign pairs: {relations['same_sign']}",
        f"- Both-zero pairs: {relations['both_zero']}",
        f"- Opposite-sign candidates per transaction (`count: transactions`): {format_distribution(opposite_counts)}",
        f"- Same-sign candidates per transaction (`count: transactions`): {format_distribution(same_counts)}",
        "",
        "## Opposite-sign Evidence",
        "",
        f"- With parsed date: {len(known_dates)}",
        f"- Without parsed date: {len(opposite_pairs) - len(known_dates)}",
        f"- Same month/day: {sum(pair.day_delta == 0 for pair in known_dates)}",
        f"- Within 1 day: {sum(abs(pair.day_delta or 0) <= 1 for pair in known_dates)}",
        f"- Within 3 days: {sum(abs(pair.day_delta or 0) <= 3 for pair in known_dates)}",
        f"- Within 7 days: {sum(abs(pair.day_delta or 0) <= 7 for pair in known_dates)}",
        f"- More than 7 days apart: {sum(abs(pair.day_delta or 0) > 7 for pair in known_dates)}",
        f"- Description contains OCR merchant: {sum(pair.description_contains_merchant for pair in opposite_pairs)}",
        f"- OCR merchant contains description: {sum(pair.merchant_contains_description for pair in opposite_pairs)}",
        "",
        "## OCR Issues",
        "",
        f"- Rows carrying issues: {sum(bool(row.issues) for row in ocr_rows)}",
    ]
    lines.extend(
        f"- `{issue}`: {count}" for issue, count in issues.most_common()
    )
    if not issues:
        lines.append("- None")

    ambiguous_transactions = sorted(
        (
            (transaction, [pair for pair in grouped.get(transaction.transaction_id, []) if pair.relation == "opposite_sign"])
            for transaction in transactions
        ),
        key=lambda item: (-len(item[1]), item[0].transaction_date, item[0].transaction_id),
    )
    ambiguous_transactions = [item for item in ambiguous_transactions if len(item[1]) > 1]
    lines.extend(
        [
            "",
            "## Transactions With Multiple Opposite-sign Candidates",
            "",
            "| Transaction | Date | Amount | Description | Candidate count | Candidate rows |",
            "| --- | --- | ---: | --- | ---: | --- |",
        ]
    )
    for transaction, transaction_pairs in ambiguous_transactions[:example_limit]:
        candidate_text = "; ".join(
            f"{pair.ocr.row_image} / {pair.ocr.merchant_text} / "
            f"day={pair.day_delta if pair.day_delta is not None else '?'} / "
            f"text={pair.text_similarity:.4f}"
            for pair in sorted(transaction_pairs, key=pair_sort_key)[:5]
        )
        lines.append(
            f"| {markdown_cell(transaction.transaction_id)} "
            f"| {transaction.transaction_date.isoformat()} "
            f"| {transaction.amount} "
            f"| {markdown_cell(transaction.description)} "
            f"| {len(transaction_pairs)} "
            f"| {markdown_cell(candidate_text)} |"
        )
    if not ambiguous_transactions:
        lines.append("| None | | | | | |")

    no_opposite = [
        transaction
        for transaction, count in zip(transactions, opposite_counts, strict=True)
        if count == 0
    ]
    lines.extend(
        [
            "",
            "## Transactions Without Opposite-sign Candidates",
            "",
            "| Transaction | Date | Amount | Description | Same-sign candidates |",
            "| --- | --- | ---: | --- | ---: |",
        ]
    )
    for transaction in no_opposite[:example_limit]:
        same_count = sum(
            pair.relation == "same_sign" for pair in grouped.get(transaction.transaction_id, [])
        )
        lines.append(
            f"| {markdown_cell(transaction.transaction_id)} "
            f"| {transaction.transaction_date.isoformat()} "
            f"| {transaction.amount} "
            f"| {markdown_cell(transaction.description)} "
            f"| {same_count} |"
        )
    if not no_opposite:
        lines.append("| None | | | | |")

    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `candidate_pairs.csv`: all equal-absolute-amount bank/OCR pairs.",
            "- `transaction_diagnostics.csv`: one row per bank transaction.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    if args.example_limit <= 0:
        raise ValueError("example-limit must be greater than zero")

    logger.info("Transactions: {}", args.transactions)
    logger.info("OCR results: {}", args.ocr)
    transactions = read_transactions(args.transactions)
    ocr_rows = read_ocr_rows(args.ocr)
    pairs = sorted(build_pairs(transactions, ocr_rows), key=pair_sort_key)
    grouped = group_pairs(pairs)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_candidate_pairs(args.output_dir / "candidate_pairs.csv", pairs)
    write_transaction_diagnostics(
        args.output_dir / "transaction_diagnostics.csv", transactions, grouped
    )
    (args.output_dir / "summary.md").write_text(
        build_summary(transactions, ocr_rows, pairs, args.example_limit) + "\n",
        encoding="utf-8",
    )

    logger.info("Bank transactions: {}", len(transactions))
    logger.info("OCR rows: {}", len(ocr_rows))
    logger.info("Candidate pairs: {}", len(pairs))
    logger.info("Summary: {}", args.output_dir / "summary.md")


if __name__ == "__main__":
    main()