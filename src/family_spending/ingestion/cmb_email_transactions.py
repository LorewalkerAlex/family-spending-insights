from __future__ import annotations

import csv
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup
from loguru import logger

from family_spending.settings import EMAILS_DIR, TRANSACTIONS_FILE

CSV_FIELDS = (
    "transaction_id",
    "transaction_date",
    "amount",
    "description",
    "source_email",
    "source_index",
)
MMDD_RE = re.compile(r"\d{4}")
POSITIVE_INTEGER_RE = re.compile(r"[1-9]\d*")
HASH_PREFIX_LENGTH = 24
TARGET_WIDTH = "643"
TARGET_HEIGHT = "18"


class CmbEmailTransactionError(RuntimeError):
    """Raised when one CMB email cannot be converted safely."""


class CmbTransactionCsvError(RuntimeError):
    """Raised when the official transaction CSV violates its data contract."""


@dataclass(frozen=True)
class CmbTransaction:
    transaction_id: str
    transaction_date: date
    amount: Decimal
    description: str
    source_email: str
    source_index: int


@dataclass(frozen=True)
class ParsedEmail:
    transactions: tuple[CmbTransaction, ...]
    skipped_repayments: int


@dataclass(frozen=True)
class RebuildSummary:
    emails: int
    transactions: int
    skipped_repayments: int
    output_path: Path


def normalize_text(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def parse_amount(value: object | None) -> Decimal:
    text = normalize_text(value)
    cleaned = text.replace("&yen;", "").replace("¥", "").replace("￥", "").replace(",", "").replace(" ", "")

    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        raise CmbEmailTransactionError(f"Invalid CMB amount: {text!r}")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CmbEmailTransactionError(f"Invalid CMB amount: {text!r}") from exc

    if not amount.is_finite():
        raise CmbEmailTransactionError(f"Invalid CMB amount: {text!r}")
    return amount


def complete_mmdd(mmdd: str, email_date: date) -> date:
    if MMDD_RE.fullmatch(mmdd) is None:
        raise CmbEmailTransactionError(f"Invalid MMDD value: {mmdd!r}")
    month = int(mmdd[:2])
    day = int(mmdd[2:])
    year = email_date.year - 1 if month > email_date.month else email_date.year

    try:
        completed = date(year, month, day)
    except ValueError as exc:
        raise CmbEmailTransactionError(f"Invalid MMDD calendar date: {mmdd!r}") from exc

    if completed > email_date:
        raise CmbEmailTransactionError(f"Completed date {completed} is later than email date {email_date}")
    return completed


def build_transaction_id(source_email: str, source_index: int) -> str:
    payload = f"cmb\0{source_email}\0{source_index}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:HASH_PREFIX_LENGTH]
    return f"cmb_{digest}"


def _parse_email_date(message: EmailMessage, source_email: str) -> date:
    header = message.get("Date")
    if header is None:
        raise CmbEmailTransactionError(f"Missing Date header in {source_email}")
    try:
        parsed = parsedate_to_datetime(str(header))
    except (TypeError, ValueError, OverflowError) as exc:
        raise CmbEmailTransactionError(f"Invalid Date header in {source_email}") from exc

    if parsed is None:
        raise CmbEmailTransactionError(f"Invalid Date header in {source_email}")
    return parsed.date()


def _decode_html_part(part: EmailMessage, source_email: str, part_index: int) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        raise CmbEmailTransactionError(f"Unable to read HTML part {part_index} in {source_email}")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError) as exc:
        raise CmbEmailTransactionError(
            f"Unable to decode HTML part {part_index} in {source_email} with charset {charset!r}"
        ) from exc


def _parse_html(html: str, source_email: str, email_date: date) -> ParsedEmail:
    soup = BeautifulSoup(html, "html.parser")
    transactions: list[CmbTransaction] = []
    skipped_repayments = 0
    for table in soup.find_all("table"):
        if normalize_text(table.get("width")) != TARGET_WIDTH or normalize_text(table.get("height")) != TARGET_HEIGHT:
            continue

        row = table.find("tr")
        if row is None:
            continue

        cells = [normalize_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td", recursive=False)]
        if len(cells) != 8:
            continue

        _, transaction_mmdd, post_mmdd, description, amount_text, _, _, _ = cells
        if not transaction_mmdd:
            if MMDD_RE.fullmatch(post_mmdd) and description and "还款" in description:
                parse_amount(amount_text)
                skipped_repayments += 1
                continue
            raise CmbEmailTransactionError(f"Unexpected date-less row in {source_email}: {description!r}")
        if MMDD_RE.fullmatch(transaction_mmdd) is None:
            raise CmbEmailTransactionError(f"Invalid transaction date in {source_email}: {transaction_mmdd!r}")
        if MMDD_RE.fullmatch(post_mmdd) is None:
            raise CmbEmailTransactionError(f"Invalid post date in {source_email}: {post_mmdd!r}")
        if not description:
            raise CmbEmailTransactionError(f"Empty description in {source_email}")
        source_index = len(transactions) + 1
        transactions.append(
            CmbTransaction(
                transaction_id=build_transaction_id(source_email, source_index),
                transaction_date=complete_mmdd(transaction_mmdd, email_date),
                amount=parse_amount(amount_text),
                description=description,
                source_email=source_email,
                source_index=source_index,
            )
        )
    return ParsedEmail(transactions=tuple(transactions), skipped_repayments=skipped_repayments)


def parse_cmb_email(raw_bytes: bytes, source_email: str) -> ParsedEmail:
    message = message_from_bytes(raw_bytes, policy=policy.default)
    email_date = _parse_email_date(message, source_email)
    candidates: list[ParsedEmail] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part_index, part in enumerate(parts, start=1):
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() != "text/html":
            continue

        parsed = _parse_html(_decode_html_part(part, source_email, part_index), source_email, email_date)
        if parsed.transactions or parsed.skipped_repayments:
            candidates.append(parsed)
    if not candidates:
        raise CmbEmailTransactionError(f"No CMB transaction table found in {source_email}")
    if len(candidates) != 1:
        raise CmbEmailTransactionError(f"Multiple CMB transaction HTML parts found in {source_email}")
    if not candidates[0].transactions:
        raise CmbEmailTransactionError(f"No CMB transactions found in {source_email}")
    return candidates[0]


def parse_cmb_email_file(path: Path) -> ParsedEmail:
    return parse_cmb_email(path.read_bytes(), path.name)


def _csv_row(transaction: CmbTransaction) -> dict[str, object]:
    return {
        "transaction_id": transaction.transaction_id,
        "transaction_date": transaction.transaction_date.isoformat(),
        "amount": format(transaction.amount, "f"),
        "description": transaction.description,
        "source_email": transaction.source_email,
        "source_index": transaction.source_index,
    }


def write_transactions_csv(transactions: tuple[CmbTransaction, ...], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            writer = csv.DictWriter(temp_file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(_csv_row(transaction) for transaction in transactions)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _require_csv_value(row: dict[str | None, str | list[str] | None], field: str, path: Path, line_number: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CmbTransactionCsvError(
            f"Invalid transaction CSV {path} at line {line_number}: field {field!r} must be a non-empty string"
        )
    return value


def _parse_csv_transaction(
    row: dict[str | None, str | list[str] | None],
    path: Path,
    line_number: int,
) -> CmbTransaction:
    extra_values = row.get(None)
    if extra_values:
        raise CmbTransactionCsvError(
            f"Invalid transaction CSV {path} at line {line_number}: unexpected extra values {extra_values!r}"
        )

    transaction_id = _require_csv_value(row, "transaction_id", path, line_number)
    transaction_date_text = _require_csv_value(row, "transaction_date", path, line_number)
    amount_text = _require_csv_value(row, "amount", path, line_number)
    description = _require_csv_value(row, "description", path, line_number)
    source_email = _require_csv_value(row, "source_email", path, line_number)
    source_index_text = _require_csv_value(row, "source_index", path, line_number)

    try:
        transaction_date = date.fromisoformat(transaction_date_text.strip())
    except ValueError as exc:
        raise CmbTransactionCsvError(
            f"Invalid transaction CSV {path} at line {line_number}: invalid transaction_date {transaction_date_text!r}"
        ) from exc

    try:
        amount = Decimal(amount_text.strip())
    except InvalidOperation as exc:
        raise CmbTransactionCsvError(
            f"Invalid transaction CSV {path} at line {line_number}: invalid amount {amount_text!r}"
        ) from exc
    if not amount.is_finite():
        raise CmbTransactionCsvError(
            f"Invalid transaction CSV {path} at line {line_number}: invalid amount {amount_text!r}"
        )

    stripped_source_index = source_index_text.strip()
    if POSITIVE_INTEGER_RE.fullmatch(stripped_source_index) is None:
        raise CmbTransactionCsvError(
            f"Invalid transaction CSV {path} at line {line_number}: invalid source_index {source_index_text!r}"
        )

    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        amount=amount,
        description=description,
        source_email=source_email,
        source_index=int(stripped_source_index),
    )


def read_transactions_csv(path: Path = TRANSACTIONS_FILE) -> tuple[CmbTransaction, ...]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            actual_fields = tuple(reader.fieldnames or ())
            if actual_fields != CSV_FIELDS:
                raise CmbTransactionCsvError(
                    f"Invalid transaction CSV header in {path}: expected {CSV_FIELDS!r}, got {actual_fields!r}"
                )

            transactions: list[CmbTransaction] = []
            transaction_id_lines: dict[str, int] = {}
            for row in reader:
                line_number = reader.line_num
                transaction = _parse_csv_transaction(row, path, line_number)
                first_line = transaction_id_lines.get(transaction.transaction_id)
                if first_line is not None:
                    raise CmbTransactionCsvError(
                        f"Duplicate transaction_id {transaction.transaction_id!r} in {path} at line {line_number}; "
                        f"first defined at line {first_line}"
                    )
                transaction_id_lines[transaction.transaction_id] = line_number
                transactions.append(transaction)
    except CmbTransactionCsvError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise CmbTransactionCsvError(f"Unable to read transaction CSV {path}: {exc}") from exc

    return tuple(transactions)


def rebuild_transactions(
    email_dir: Path = EMAILS_DIR,
    output_path: Path = TRANSACTIONS_FILE,
) -> RebuildSummary:
    email_paths = sorted(email_dir.glob("*.eml"), key=lambda path: path.name)
    if not email_paths:
        raise CmbEmailTransactionError(f"No .eml files found in {email_dir}")
    logger.info(
        "Starting CMB transaction rebuild: emails={}, input={}, output={}",
        len(email_paths),
        email_dir,
        output_path,
    )

    transactions: list[CmbTransaction] = []
    skipped_repayments = 0
    for email_path in email_paths:
        parsed = parse_cmb_email_file(email_path)
        transactions.extend(parsed.transactions)
        skipped_repayments += parsed.skipped_repayments
        logger.info(
            "CMB email parsed: file={}, transactions={}, skipped_repayments={}",
            email_path.name,
            len(parsed.transactions),
            parsed.skipped_repayments,
        )
    transaction_ids = [transaction.transaction_id for transaction in transactions]
    if len(transaction_ids) != len(set(transaction_ids)):
        raise CmbEmailTransactionError("Duplicate transaction_id detected")

    transactions.sort(key=lambda item: (item.transaction_date, item.source_email, item.source_index))
    write_transactions_csv(tuple(transactions), output_path)
    summary = RebuildSummary(
        emails=len(email_paths),
        transactions=len(transactions),
        skipped_repayments=skipped_repayments,
        output_path=output_path,
    )
    logger.info(
        "CMB transaction rebuild completed: emails={}, transactions={}, skipped_repayments={}, output={}",
        summary.emails,
        summary.transactions,
        summary.skipped_repayments,
        summary.output_path,
    )
    return summary


def main() -> None:
    rebuild_transactions()


if __name__ == "__main__":
    main()
