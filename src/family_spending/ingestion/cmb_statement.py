from __future__ import annotations

import csv
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from loguru import logger

CSV_FIELDS = (
    "source_email",
    "email_date",
    "transaction_index",
    "transaction_mmdd",
    "post_mmdd",
    "description",
    "raw_amount_text",
    "raw_amount",
    "card_last4",
    "country_or_region",
    "cny_amount_text",
    "cny_amount",
)


class CmbStatementError(RuntimeError):
    """Base error for one local CMB statement email."""


class CmbStatementDecodeError(CmbStatementError):
    """Raised when a statement HTML MIME part cannot be decoded safely."""


class CmbStatementAmbiguityError(CmbStatementError):
    """Raised when different HTML parts contain different transaction rows."""


@dataclass(frozen=True)
class CmbRawTransaction:
    """Keep CMB-specific raw fields before any cleaning or Mapping."""

    transaction_mmdd: str
    post_mmdd: str
    description: str
    raw_amount_text: str
    raw_amount: Decimal | None
    card_last4: str
    country_or_region: str
    cny_amount_text: str
    cny_amount: Decimal | None


@dataclass(frozen=True)
class CmbStatement:
    """Represent one parsed source email and its raw CMB transaction rows."""

    source_email: str
    email_date: date
    message_id: str | None
    transactions: tuple[CmbRawTransaction, ...]


@dataclass
class CmbStatementBatchResult:
    """Keep counters needed to verify one local statement extraction run."""

    emails: int = 0
    parsed: int = 0
    written: int = 0
    empty: int = 0
    failed: int = 0
    transactions: int = 0


@dataclass(frozen=True)
class CmbStatementConfig:
    """Keep runtime paths explicit so imports never touch local data."""

    email_dir: Path
    statement_dir: Path


def normalize_text(value: object | None) -> str:
    """Apply only structural whitespace cleanup to visible statement text."""

    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_amount(value: object | None) -> Decimal | None:
    """Parse a CMB amount without applying any expense or refund meaning."""

    text = normalize_text(value)
    if not text:
        return None

    cleaned = (
        text.replace("&yen;", "")
        .replace("¥", "")
        .replace("￥", "")
        .replace(",", "")
        .replace(" ", "")
    )
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        return None

    try:
        # Decimal preserves the bank's decimal value without float rounding.
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def extract_cmb_transactions_from_html(html: str) -> tuple[CmbRawTransaction, ...]:
    """Extract only the currently verified CMB 643-by-18 transaction rows."""

    soup = BeautifulSoup(html, "html.parser")
    transactions: list[CmbRawTransaction] = []

    for table in soup.find_all("table"):
        if normalize_text(table.get("width")) != "643":
            continue
        if normalize_text(table.get("height")) != "18":
            continue

        row = table.find("tr")
        if row is None:
            continue

        cells = [
            normalize_text(cell.get_text(" ", strip=True))
            for cell in row.find_all("td", recursive=False)
        ]
        if len(cells) != 8:
            continue

        (
            _,
            transaction_mmdd,
            post_mmdd,
            description,
            raw_amount_text,
            card_last4,
            country_or_region,
            cny_amount_text,
        ) = cells

        if not re.fullmatch(r"\d{4}", transaction_mmdd):
            continue
        if not re.fullmatch(r"\d{4}", post_mmdd):
            continue
        if not description:
            continue

        raw_amount = parse_amount(raw_amount_text)
        cny_amount = parse_amount(cny_amount_text)
        if raw_amount is None and cny_amount is None:
            continue

        transactions.append(
            CmbRawTransaction(
                transaction_mmdd=transaction_mmdd,
                post_mmdd=post_mmdd,
                description=description,
                raw_amount_text=raw_amount_text,
                raw_amount=raw_amount,
                card_last4=card_last4,
                country_or_region=country_or_region,
                cny_amount_text=cny_amount_text,
                cny_amount=cny_amount,
            )
        )

    return tuple(transactions)


def _decode_html_part(part: EmailMessage, source_email: str, part_index: int) -> str:
    try:
        content = part.get_content()
    except Exception as content_error:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise CmbStatementDecodeError(
                f"Unable to decode HTML MIME part {part_index} in {source_email}"
            ) from content_error

        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset)
        except (LookupError, UnicodeDecodeError) as decode_error:
            raise CmbStatementDecodeError(
                f"Unable to decode HTML MIME part {part_index} in {source_email} "
                f"with charset {charset!r}"
            ) from decode_error

    if not isinstance(content, str):
        raise CmbStatementDecodeError(
            f"HTML MIME part {part_index} in {source_email} did not decode to text"
        )

    if "\ufffd" in content:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise CmbStatementDecodeError(
                f"HTML MIME part {part_index} in {source_email} contains replacement text"
            )

        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset)
        except (LookupError, UnicodeDecodeError) as decode_error:
            raise CmbStatementDecodeError(
                f"Unable to strictly decode HTML MIME part {part_index} in "
                f"{source_email} with charset {charset!r}"
            ) from decode_error

    return content


def _parse_email_date(message: EmailMessage, source_email: str) -> date:
    header = message.get("Date")
    if header is None:
        raise CmbStatementError(f"Missing Date header in {source_email}")

    try:
        parsed = parsedate_to_datetime(str(header))
    except (TypeError, ValueError, OverflowError) as exc:
        raise CmbStatementError(f"Invalid Date header in {source_email}") from exc

    if parsed is None:
        raise CmbStatementError(f"Invalid Date header in {source_email}")
    return parsed.date()


def parse_cmb_statement(raw_bytes: bytes, source_email: str) -> CmbStatement:
    """Parse one immutable RFC822 message without changing its source bytes."""

    message = message_from_bytes(raw_bytes, policy=policy.default)
    email_date = _parse_email_date(message, source_email)
    message_id = str(message["Message-ID"]) if message.get("Message-ID") else None

    candidates: list[tuple[CmbRawTransaction, ...]] = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part_index, part in enumerate(parts, start=1):
        if part.is_multipart():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() != "text/html":
            continue

        html = _decode_html_part(part, source_email, part_index)
        transactions = extract_cmb_transactions_from_html(html)
        if transactions:
            candidates.append(transactions)

    if not candidates:
        selected: tuple[CmbRawTransaction, ...] = ()
    else:
        selected = candidates[0]
        # HTML alternatives are evaluated independently; concatenating them can duplicate rows.
        if any(candidate != selected for candidate in candidates[1:]):
            raise CmbStatementAmbiguityError(
                f"Different HTML MIME parts contain different CMB transactions in {source_email}"
            )

    return CmbStatement(
        source_email=source_email,
        email_date=email_date,
        message_id=message_id,
        transactions=selected,
    )


def parse_cmb_statement_file(path: Path) -> CmbStatement:
    """Read one local .eml as bytes and preserve the original file unchanged."""

    return parse_cmb_statement(path.read_bytes(), path.name)


def write_statement_csv(statement: CmbStatement, output_path: Path) -> None:
    """Atomically rebuild one derived CSV so a failed write preserves the old file."""

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

            for transaction_index, transaction in enumerate(
                statement.transactions,
                start=1,
            ):
                writer.writerow(
                    {
                        "source_email": statement.source_email,
                        "email_date": statement.email_date.isoformat(),
                        "transaction_index": transaction_index,
                        "transaction_mmdd": transaction.transaction_mmdd,
                        "post_mmdd": transaction.post_mmdd,
                        "description": transaction.description,
                        "raw_amount_text": transaction.raw_amount_text,
                        "raw_amount": (
                            format(transaction.raw_amount, "f")
                            if transaction.raw_amount is not None
                            else ""
                        ),
                        "card_last4": transaction.card_last4,
                        "country_or_region": transaction.country_or_region,
                        "cny_amount_text": transaction.cny_amount_text,
                        "cny_amount": (
                            format(transaction.cny_amount, "f")
                            if transaction.cny_amount is not None
                            else ""
                        ),
                    }
                )

            temp_file.flush()
            os.fsync(temp_file.fileno())

        # Derived CSV files are replaceable, but only after a complete temp file exists.
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def process_cmb_statements(
    email_dir: Path,
    statement_dir: Path,
) -> CmbStatementBatchResult:
    """Parse sorted local emails independently and continue after per-file failures."""

    result = CmbStatementBatchResult()
    email_paths = sorted(email_dir.glob("*.eml"), key=lambda path: path.name)

    for email_path in email_paths:
        result.emails += 1
        try:
            statement = parse_cmb_statement_file(email_path)
            result.parsed += 1
            if not statement.transactions:
                result.empty += 1

            output_path = statement_dir / f"{email_path.stem}.csv"
            write_statement_csv(statement, output_path)
            result.written += 1
            result.transactions += len(statement.transactions)
            logger.info(
                "Statement written: file={}, transactions={}, empty={}",
                email_path.name,
                len(statement.transactions),
                not statement.transactions,
            )
        except Exception as exc:
            result.failed += 1
            if isinstance(exc, CmbStatementError):
                logger.error(
                    "Statement failed: file={}, error_type={}, reason={}",
                    email_path.name,
                    type(exc).__name__,
                    exc,
                )
            else:
                logger.error(
                    "Statement failed: file={}, error_type={}",
                    email_path.name,
                    type(exc).__name__,
                )

    return result


def load_config(environ: Mapping[str, str] | None = None) -> CmbStatementConfig:
    """Read .env only at the executable boundary and return explicit paths."""

    if environ is None:
        load_dotenv()
        environ = os.environ

    email_dir = environ.get("CMB_EMAIL_DIR", "data/emails/163/cmb").strip()
    statement_dir = environ.get("CMB_STATEMENT_DIR", "data/statements/cmb").strip()
    return CmbStatementConfig(
        email_dir=Path(email_dir or "data/emails/163/cmb"),
        statement_dir=Path(statement_dir or "data/statements/cmb"),
    )


def configure_logging() -> None:
    """Write concise operational logs to stdout without exposing statement data."""

    logger.remove()
    logger.add(
        sys.stdout,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<level>{message}</level>"
        ),
        colorize=sys.stdout.isatty(),
        backtrace=False,
        diagnose=False,
    )


def main() -> None:
    """Load runtime paths, process local emails, and report safe batch counters."""

    configure_logging()
    config = load_config()
    result = process_cmb_statements(config.email_dir, config.statement_dir)
    logger.info(
        "Statement extraction completed: emails={}, parsed={}, written={}, "
        "empty={}, failed={}, transactions={}",
        result.emails,
        result.parsed,
        result.written,
        result.empty,
        result.failed,
        result.transactions,
    )


if __name__ == "__main__":
    main()