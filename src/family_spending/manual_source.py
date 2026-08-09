from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from family_spending.source_records import SourceAdapter, SourceRecord, TransactionType

MANUAL_SOURCE_TYPE = "manual"
MANUAL_CURRENCY = "CNY"
MANUAL_SOURCE_RECORDS_FILE = Path("data/manual_source_records.jsonl")


class ManualSourceDataError(RuntimeError):
    """Raised when persisted or submitted Manual Source data violates its source contract."""


@dataclass(frozen=True)
class ManualSourceEntry:
    id: str
    transaction_type: TransactionType
    transaction_date: date
    amount: Decimal
    currency: str = MANUAL_CURRENCY
    # Keep the legacy optional-field order stable for any positional construction during migration.
    # New Application/API commands populate description + note, not merchant/category.
    merchant_name: str | None = None
    category: str | None = None
    note: str | None = None
    description: str | None = None


class ManualSourceAdapter(SourceAdapter[ManualSourceEntry, None]):
    def adapt(self, item: ManualSourceEntry) -> SourceRecord[None]:
        """Preserve the user-entered description as source evidence; Enrichment stays downstream."""
        _validate_entry(item)
        return SourceRecord(
            id=item.id,
            source_type=MANUAL_SOURCE_TYPE,
            transaction_type=item.transaction_type,
            transaction_date=item.transaction_date,
            amount=item.amount,
            currency=item.currency,
            description=item.description,
            provenance=None,
        )


def _optional_text(value: object, field: str) -> str | None:
    """Treat omitted or blank optional input as absent while rejecting non-text persisted values."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ManualSourceDataError(f"Manual {field} must be a string or null, got {value!r}")
    stripped = value.strip()
    return stripped or None


def _validate_entry(entry: ManualSourceEntry) -> None:
    """Keep Manual Source facts strict because later deduplication assumes stable identity and exact amounts."""
    if not entry.id.strip():
        raise ManualSourceDataError("Manual source id must not be empty")
    if entry.transaction_type not in ("income", "expense"):
        raise ManualSourceDataError(
            f"Manual transaction_type must be 'income' or 'expense', got {entry.transaction_type!r}"
        )
    if not entry.amount.is_finite():
        raise ManualSourceDataError(f"Manual amount must be finite, got {entry.amount!r}")
    if not entry.currency.strip():
        raise ManualSourceDataError("Manual currency must not be empty")


def create_manual_source_entry(
    *,
    transaction_type: TransactionType,
    transaction_date: date,
    amount: Decimal,
    description: str | None = None,
    merchant_name: str | None = None,
    category: str | None = None,
    note: str | None = None,
    currency: str = MANUAL_CURRENCY,
    source_record_id: str | None = None,
) -> ManualSourceEntry:
    """Create one immutable Manual Source input while preserving its source-native description."""
    entry = ManualSourceEntry(
        id=source_record_id or f"manual_{uuid.uuid4().hex}",
        transaction_type=transaction_type,
        transaction_date=transaction_date,
        amount=amount,
        currency=currency.strip().upper(),
        description=_optional_text(description, "description"),
        merchant_name=_optional_text(merchant_name, "merchant_name"),
        category=_optional_text(category, "category"),
        note=_optional_text(note, "note"),
    )
    _validate_entry(entry)
    return entry


def _parse_entry(raw: object, *, path: Path, line_number: int) -> ManualSourceEntry:
    """Validate every persisted field so a malformed local record cannot partially enter reconciliation."""
    if not isinstance(raw, dict):
        raise ManualSourceDataError(
            f"Invalid Manual Source record in {path} at line {line_number}: expected object"
        )
    allowed = {
        "id",
        "type",
        "date",
        "amount",
        "currency",
        "description",
        "merchant",
        "category",
        "note",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ManualSourceDataError(
            f"Invalid Manual Source record in {path} at line {line_number}: unknown fields {unknown!r}"
        )
    source_id = raw.get("id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ManualSourceDataError(
            f"Invalid Manual Source id in {path} at line {line_number}: {source_id!r}"
        )
    transaction_type = raw.get("type")
    if transaction_type not in ("income", "expense"):
        raise ManualSourceDataError(
            f"Invalid Manual Source type in {path} at line {line_number}: {transaction_type!r}"
        )
    raw_date = raw.get("date")
    if not isinstance(raw_date, str):
        raise ManualSourceDataError(
            f"Invalid Manual Source date in {path} at line {line_number}: {raw_date!r}"
        )
    try:
        transaction_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ManualSourceDataError(
            f"Invalid Manual Source date in {path} at line {line_number}: {raw_date!r}"
        ) from exc
    raw_amount = raw.get("amount")
    if not isinstance(raw_amount, str):
        raise ManualSourceDataError(
            f"Invalid Manual Source amount in {path} at line {line_number}: {raw_amount!r}"
        )
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation as exc:
        raise ManualSourceDataError(
            f"Invalid Manual Source amount in {path} at line {line_number}: {raw_amount!r}"
        ) from exc
    currency = raw.get("currency", MANUAL_CURRENCY)
    if not isinstance(currency, str) or not currency.strip():
        raise ManualSourceDataError(
            f"Invalid Manual Source currency in {path} at line {line_number}: {currency!r}"
        )
    entry = ManualSourceEntry(
        id=source_id,
        transaction_type=transaction_type,
        transaction_date=transaction_date,
        amount=amount,
        currency=currency.strip().upper(),
        description=_optional_text(raw.get("description"), "description"),
        merchant_name=_optional_text(raw.get("merchant"), "merchant"),
        category=_optional_text(raw.get("category"), "category"),
        note=_optional_text(raw.get("note"), "note"),
    )
    _validate_entry(entry)
    return entry


def read_manual_source_entries(
    path: Path = MANUAL_SOURCE_RECORDS_FILE,
) -> tuple[ManualSourceEntry, ...]:
    """Treat a missing file as an empty Manual Source so existing CMB-only installations keep working unchanged."""
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ManualSourceDataError(f"Unable to read Manual Source file {path}: {exc}") from exc
    entries: list[ManualSourceEntry] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ManualSourceDataError(
                f"Unable to parse Manual Source file {path} at line {line_number}: {exc.msg}"
            ) from exc
        entry = _parse_entry(raw, path=path, line_number=line_number)
        if entry.id in seen_ids:
            raise ManualSourceDataError(
                f"Duplicate Manual Source id {entry.id!r} in {path} at line {line_number}"
            )
        seen_ids.add(entry.id)
        entries.append(entry)
    return tuple(entries)


def _encode_entry(entry: ManualSourceEntry) -> str:
    """Persist raw description while retaining compatibility with any earlier Manual records."""
    payload: dict[str, object] = {
        "id": entry.id,
        "type": entry.transaction_type,
        "date": entry.transaction_date.isoformat(),
        "amount": format(entry.amount, "f"),
        "currency": entry.currency,
        "description": entry.description,
        "note": entry.note,
    }
    if entry.merchant_name is not None:
        payload["merchant"] = entry.merchant_name
    if entry.category is not None:
        payload["category"] = entry.category
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def write_manual_source_entries(
    entries: tuple[ManualSourceEntry, ...],
    path: Path = MANUAL_SOURCE_RECORDS_FILE,
) -> None:
    """Replace the local Manual Source file atomically so a failed write cannot leave truncated source facts."""
    for entry in entries:
        _validate_entry(entry)
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ManualSourceDataError("Manual Source entries contain duplicate ids")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{_encode_entry(entry)}\n" for entry in entries)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
