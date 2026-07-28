from __future__ import annotations

import csv
import hashlib
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

SOURCE = "cmb"
HASH_PREFIX_LENGTH = 24
INPUT_FIELDS = (
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
OUTPUT_FIELDS = (
    "transaction_id",
    "source",
    "source_email",
    "source_transaction_index",
    "email_date",
    "transaction_date",
    "post_date",
    "bank_description",
    "payment_channel",
    "merchant_raw",
    "raw_amount_text",
    "raw_amount",
    "cny_amount_text",
    "cny_amount",
    "card_last4",
    "country_or_region",
)
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MMDD_RE = re.compile(r"[0-9]{4}")
POSITIVE_INTEGER_RE = re.compile(r"[1-9][0-9]*")


class CmbCleaningError(RuntimeError):
    """Base error for one CMB statement cleaning operation."""


class CmbStatementContractError(CmbCleaningError):
    """Raised when one statement CSV violates the upstream contract."""


class CmbTransactionIdCollisionError(CmbCleaningError):
    """Raised when structural transaction identities are not unique."""


@dataclass(frozen=True)
class CmbCleanedTransaction:
    """Represent one cleaned CMB transaction before Mapping is applied."""

    transaction_id: str
    source: str
    source_email: str
    source_transaction_index: int
    email_date: date
    transaction_date: date
    post_date: date
    bank_description: str
    payment_channel: str
    merchant_raw: str
    raw_amount_text: str
    raw_amount: Decimal | None
    cny_amount_text: str
    cny_amount: Decimal | None
    card_last4: str
    country_or_region: str


@dataclass
class CmbCleaningBatchResult:
    """Keep counters needed to verify one statement cleaning run."""

    statements: int = 0
    cleaned: int = 0
    written: int = 0
    empty: int = 0
    failed: int = 0
    transactions: int = 0


@dataclass(frozen=True)
class CmbCleaningConfig:
    """Keep runtime paths explicit so imports never touch local data."""

    statement_dir: Path
    transaction_dir: Path


def build_transaction_id(source_email: str, source_transaction_index: int) -> str:
    """Build a stable ID from source identity fields only."""

    payload = f"{SOURCE}\0{source_email}\0{source_transaction_index}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:HASH_PREFIX_LENGTH]
    return f"{SOURCE}_{digest}"


def complete_mmdd(mmdd: str, email_date: date) -> date:
    """Complete one MMDD value using the statement email date as year anchor."""

    if MMDD_RE.fullmatch(mmdd) is None:
        raise CmbStatementContractError("invalid MMDD format")

    month = int(mmdd[:2])
    day = int(mmdd[2:])
    year = email_date.year - 1 if month > email_date.month else email_date.year
    try:
        completed = date(year, month, day)
    except ValueError as exc:
        raise CmbStatementContractError("invalid MMDD calendar date") from exc

    if completed > email_date:
        raise CmbStatementContractError("completed date is later than email_date")
    return completed


def _parse_email_date(value: str) -> date:
    if DATE_RE.fullmatch(value) is None:
        raise CmbStatementContractError("invalid email_date format")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CmbStatementContractError("invalid email_date calendar date") from exc


def _parse_amount(value: str, field_name: str) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise CmbStatementContractError(f"invalid {field_name}") from exc
    if not amount.is_finite():
        raise CmbStatementContractError(f"invalid {field_name}")
    return amount


def _validate_input_fields(fieldnames: list[str] | None) -> None:
    present = set(fieldnames or ())
    if any(field not in present for field in INPUT_FIELDS):
        raise CmbStatementContractError("missing required input fields")


def read_cmb_statement_csv(path: Path) -> tuple[CmbCleanedTransaction, ...]:
    """Validate and clean one complete statement CSV or fail the whole file."""

    transactions: list[CmbCleanedTransaction] = []
    file_source_email: str | None = None
    file_email_date: date | None = None
    seen_indexes: set[int] = set()
    seen_transaction_ids: set[str] = set()

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        _validate_input_fields(reader.fieldnames)
        for row_number, row in enumerate(reader, start=2):
            source_email = row["source_email"] or ""
            if not source_email.strip():
                raise CmbStatementContractError(
                    f"empty source_email at CSV row {row_number}"
                )
            if file_source_email is None:
                file_source_email = source_email
                if Path(source_email).stem != path.stem:
                    raise CmbStatementContractError(
                        "source_email stem does not match statement filename"
                    )
            elif source_email != file_source_email:
                raise CmbStatementContractError(
                    "source_email is inconsistent within statement"
                )

            email_date = _parse_email_date(row["email_date"] or "")
            if file_email_date is None:
                file_email_date = email_date
            elif email_date != file_email_date:
                raise CmbStatementContractError(
                    "email_date is inconsistent within statement"
                )

            index_text = row["transaction_index"] or ""
            if POSITIVE_INTEGER_RE.fullmatch(index_text) is None:
                raise CmbStatementContractError(
                    f"invalid transaction_index at CSV row {row_number}"
                )
            transaction_index = int(index_text)
            if transaction_index in seen_indexes:
                raise CmbStatementContractError("duplicate transaction_index")
            expected_index = len(transactions) + 1
            if transaction_index != expected_index:
                raise CmbStatementContractError(
                    "transaction_index must be consecutive from 1 in input order"
                )
            seen_indexes.add(transaction_index)

            transaction_mmdd = row["transaction_mmdd"] or ""
            post_mmdd = row["post_mmdd"] or ""
            if MMDD_RE.fullmatch(transaction_mmdd) is None:
                raise CmbStatementContractError("invalid transaction_mmdd")
            if MMDD_RE.fullmatch(post_mmdd) is None:
                raise CmbStatementContractError("invalid post_mmdd")

            bank_description = (row["description"] or "").strip()
            if not bank_description:
                raise CmbStatementContractError(
                    f"empty description at CSV row {row_number}"
                )

            raw_amount = _parse_amount(row["raw_amount"] or "", "raw_amount")
            cny_amount = _parse_amount(row["cny_amount"] or "", "cny_amount")
            if raw_amount is None and cny_amount is None:
                raise CmbStatementContractError(
                    f"missing numeric amount at CSV row {row_number}"
                )

            transaction_id = build_transaction_id(source_email, transaction_index)
            if transaction_id in seen_transaction_ids:
                raise CmbTransactionIdCollisionError(
                    "duplicate transaction_id within statement"
                )
            seen_transaction_ids.add(transaction_id)

            transactions.append(
                CmbCleanedTransaction(
                    transaction_id=transaction_id,
                    source=SOURCE,
                    source_email=source_email,
                    source_transaction_index=transaction_index,
                    email_date=email_date,
                    transaction_date=complete_mmdd(transaction_mmdd, email_date),
                    post_date=complete_mmdd(post_mmdd, email_date),
                    bank_description=bank_description,
                    payment_channel="",
                    merchant_raw=bank_description,
                    raw_amount_text=row["raw_amount_text"] or "",
                    raw_amount=raw_amount,
                    cny_amount_text=row["cny_amount_text"] or "",
                    cny_amount=cny_amount,
                    card_last4=row["card_last4"] or "",
                    country_or_region=row["country_or_region"] or "",
                )
            )

    return tuple(transactions)


def _format_amount(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _output_row(transaction: CmbCleanedTransaction) -> dict[str, object]:
    return {
        "transaction_id": transaction.transaction_id,
        "source": transaction.source,
        "source_email": transaction.source_email,
        "source_transaction_index": transaction.source_transaction_index,
        "email_date": transaction.email_date.isoformat(),
        "transaction_date": transaction.transaction_date.isoformat(),
        "post_date": transaction.post_date.isoformat(),
        "bank_description": transaction.bank_description,
        "payment_channel": transaction.payment_channel,
        "merchant_raw": transaction.merchant_raw,
        "raw_amount_text": transaction.raw_amount_text,
        "raw_amount": _format_amount(transaction.raw_amount),
        "cny_amount_text": transaction.cny_amount_text,
        "cny_amount": _format_amount(transaction.cny_amount),
        "card_last4": transaction.card_last4,
        "country_or_region": transaction.country_or_region,
    }


def write_cleaned_transactions_csv(
    transactions: tuple[CmbCleanedTransaction, ...],
    output_path: Path,
) -> None:
    """Atomically rebuild one deterministic cleaned transaction CSV."""

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
            writer = csv.DictWriter(temp_file, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for transaction in transactions:
                writer.writerow(_output_row(transaction))
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def process_cmb_transactions(
    statement_dir: Path,
    transaction_dir: Path,
) -> CmbCleaningBatchResult:
    """Clean sorted statement files independently and continue after failures."""

    result = CmbCleaningBatchResult()
    seen_source_identities: set[tuple[str, int]] = set()
    seen_transaction_ids: set[str] = set()
    statement_paths = sorted(statement_dir.glob("*.csv"), key=lambda path: path.name)

    for statement_path in statement_paths:
        result.statements += 1
        try:
            transactions = read_cmb_statement_csv(statement_path)
            source_identities = {
                (transaction.source_email, transaction.source_transaction_index)
                for transaction in transactions
            }
            transaction_ids = {
                transaction.transaction_id for transaction in transactions
            }
            if len(source_identities) != len(transactions):
                raise CmbTransactionIdCollisionError(
                    "duplicate source transaction identity within statement"
                )
            if len(transaction_ids) != len(transactions):
                raise CmbTransactionIdCollisionError(
                    "duplicate transaction_id within statement"
                )
            if seen_source_identities.intersection(source_identities):
                raise CmbTransactionIdCollisionError(
                    "duplicate source transaction identity across statements"
                )
            if seen_transaction_ids.intersection(transaction_ids):
                raise CmbTransactionIdCollisionError(
                    "duplicate transaction_id across statements"
                )

            result.cleaned += 1
            output_path = transaction_dir / f"{statement_path.stem}.csv"
            write_cleaned_transactions_csv(transactions, output_path)
            seen_source_identities.update(source_identities)
            seen_transaction_ids.update(transaction_ids)
            result.written += 1
            if not transactions:
                result.empty += 1
            result.transactions += len(transactions)
            logger.info(
                "Transactions written: file={}, transactions={}, empty={}",
                statement_path.name,
                len(transactions),
                not transactions,
            )
        except Exception as exc:
            result.failed += 1
            if isinstance(exc, CmbCleaningError):
                logger.error(
                    "Transaction cleaning failed: file={}, error_type={}, reason={}",
                    statement_path.name,
                    type(exc).__name__,
                    exc,
                )
            else:
                logger.error(
                    "Transaction cleaning failed: file={}, error_type={}",
                    statement_path.name,
                    type(exc).__name__,
                )

    return result


def load_config(environ: Mapping[str, str] | None = None) -> CmbCleaningConfig:
    """Read .env only at the executable boundary and return explicit paths."""

    if environ is None:
        load_dotenv()
        environ = os.environ
    statement_dir = environ.get(
        "CMB_STATEMENT_DIR", "data/statements/cmb"
    ).strip()
    transaction_dir = environ.get(
        "CMB_TRANSACTION_DIR", "data/transactions/cmb"
    ).strip()
    return CmbCleaningConfig(
        statement_dir=Path(statement_dir or "data/statements/cmb"),
        transaction_dir=Path(transaction_dir or "data/transactions/cmb"),
    )


def configure_logging() -> None:
    """Write concise operational logs to stdout without transaction details."""

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
    """Load paths, clean local statements, and report safe batch counters."""

    configure_logging()
    config = load_config()
    result = process_cmb_transactions(config.statement_dir, config.transaction_dir)
    logger.info(
        "Transaction cleaning completed: statements={}, cleaned={}, written={}, "
        "empty={}, failed={}, transactions={}",
        result.statements,
        result.cleaned,
        result.written,
        result.empty,
        result.failed,
        result.transactions,
    )


if __name__ == "__main__":
    main()
