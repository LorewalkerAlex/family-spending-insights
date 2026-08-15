from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from family_spending.domain.errors import DomainInvariantError
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.sources.manual.model import ManualEvidence


class ManualEvidenceStoreError(RuntimeError):
    """Raised when canonical Manual Source evidence cannot be loaded or persisted safely."""


_ALLOWED_FIELDS = {"id", "type", "date", "amount", "currency", "description"}


def _parse_record(raw: object, *, path: Path, line_number: int) -> ManualEvidence:
    """Fail closed on legacy enrichment fields instead of silently importing mixed semantics."""
    if not isinstance(raw, dict):
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence in {path} at line {line_number}: expected object"
        )
    unknown = sorted(set(raw) - _ALLOWED_FIELDS)
    if unknown:
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence in {path} at line {line_number}: unknown fields {unknown!r}"
        )

    evidence_id = raw.get("id")
    transaction_type = raw.get("type")
    raw_date = raw.get("date")
    raw_amount = raw.get("amount")
    currency = raw.get("currency")
    description = raw.get("description")

    if not isinstance(evidence_id, str):
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence id in {path} at line {line_number}: {evidence_id!r}"
        )
    if transaction_type not in ("income", "expense"):
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence type in {path} at line {line_number}: {transaction_type!r}"
        )
    if not isinstance(raw_date, str):
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence date in {path} at line {line_number}: {raw_date!r}"
        )
    try:
        transaction_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence date in {path} at line {line_number}: {raw_date!r}"
        ) from exc
    if not isinstance(raw_amount, str):
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence amount in {path} at line {line_number}: {raw_amount!r}"
        )
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation as exc:
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence amount in {path} at line {line_number}: {raw_amount!r}"
        ) from exc
    if not isinstance(currency, str):
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence currency in {path} at line {line_number}: {currency!r}"
        )
    if description is not None and not isinstance(description, str):
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence description in {path} at line {line_number}: {description!r}"
        )

    try:
        return ManualEvidence(
            evidence_id=evidence_id,
            transaction_type=transaction_type,
            transaction_date=transaction_date,
            amount=amount,
            currency=currency,
            description=description,
        )
    except DomainInvariantError as exc:
        raise ManualEvidenceStoreError(
            f"Invalid Manual evidence in {path} at line {line_number}: {exc}"
        ) from exc


def _encode_record(record: ManualEvidence) -> str:
    payload = {
        "id": record.evidence_id,
        "type": record.transaction_type,
        "date": record.transaction_date.isoformat(),
        "amount": format(record.amount, "f"),
        "currency": record.currency,
        "description": record.description,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class ManualEvidenceStore:
    """Persist only source-native Manual facts in canonical evidence/manual/records.jsonl."""

    layout: StorageLayout

    @property
    def path(self) -> Path:
        return self.layout.manual_evidence

    def load_all(self) -> tuple[ManualEvidence, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ManualEvidenceStoreError(
                f"Unable to read Manual evidence {self.path}: {exc}"
            ) from exc

        records: list[ManualEvidence] = []
        seen_ids: set[str] = set()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ManualEvidenceStoreError(
                    f"Unable to parse Manual evidence {self.path} at line {line_number}: {exc.msg}"
                ) from exc
            record = _parse_record(raw, path=self.path, line_number=line_number)
            if record.evidence_id in seen_ids:
                raise ManualEvidenceStoreError(
                    f"Duplicate Manual evidence id {record.evidence_id!r} "
                    f"in {self.path} at line {line_number}"
                )
            seen_ids.add(record.evidence_id)
            records.append(record)
        return tuple(records)

    def replace_all(self, records: tuple[ManualEvidence, ...]) -> None:
        """Atomically persist a complete canonical evidence set for controlled write paths/migration."""
        ids = [record.evidence_id for record in records]
        if len(ids) != len(set(ids)):
            raise ManualEvidenceStoreError("Manual evidence contains duplicate ids")
        if not records:
            self.path.unlink(missing_ok=True)
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(f"{_encode_record(record)}\n" for record in records)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, self.path)
            temp_path = None
        except OSError as exc:
            raise ManualEvidenceStoreError(
                f"Unable to persist Manual evidence {self.path}: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
