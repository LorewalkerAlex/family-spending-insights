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
HASH_PREFIX_LENGTH = 24
TARGET_WIDTH = "643"
TARGET_HEIGHT = "18"


class CmbEmailTransactionError(RuntimeError):
    """Raised when one CMB email cannot be converted safely."""


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