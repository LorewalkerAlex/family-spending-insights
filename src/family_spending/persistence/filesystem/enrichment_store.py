from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from family_spending.domain.enrichment import EnrichmentDecision
from family_spending.domain.errors import DomainInvariantError
from family_spending.persistence.filesystem.layout import StorageLayout


class EnrichmentDecisionStoreError(RuntimeError):
    """Raised when sparse durable Enrichment decisions are malformed or cannot be persisted."""


_ALLOWED_FIELDS = {
    "transaction_id",
    "merchant_override",
    "category_override",
    "note",
}


def _parse_decision(raw: object, *, path: Path, line_number: int) -> EnrichmentDecision:
    """Accept only sparse canonical decision fields and reject materialized legacy Enrichment state."""
    if not isinstance(raw, dict):
        raise EnrichmentDecisionStoreError(
            f"Invalid EnrichmentDecision in {path} at line {line_number}: expected object"
        )
    fields = set(raw)
    if "transaction_id" not in fields or not fields <= _ALLOWED_FIELDS:
        raise EnrichmentDecisionStoreError(
            f"Invalid EnrichmentDecision fields in {path} at line {line_number}: {sorted(fields)!r}"
        )
    transaction_id = raw["transaction_id"]
    if not isinstance(transaction_id, str):
        raise EnrichmentDecisionStoreError(
            f"Invalid transaction_id in {path} at line {line_number}: {transaction_id!r}"
        )
    for field in ("merchant_override", "category_override", "note"):
        value = raw.get(field)
        if value is not None and not isinstance(value, str):
            raise EnrichmentDecisionStoreError(
                f"Invalid {field} in {path} at line {line_number}: {value!r}"
            )
    try:
        return EnrichmentDecision(
            transaction_id=transaction_id,
            merchant_override=raw.get("merchant_override"),
            category_override=raw.get("category_override"),
            note=raw.get("note"),
        )
    except DomainInvariantError as exc:
        raise EnrichmentDecisionStoreError(
            f"Invalid EnrichmentDecision in {path} at line {line_number}: {exc}"
        ) from exc


def _encode_decision(decision: EnrichmentDecision) -> str:
    """Serialize only fields the user actually decided so Mapping-derived state is never duplicated."""
    payload: dict[str, str] = {"transaction_id": decision.transaction_id}
    if decision.merchant_override is not None:
        payload["merchant_override"] = decision.merchant_override
    if decision.category_override is not None:
        payload["category_override"] = decision.category_override
    if decision.note is not None:
        payload["note"] = decision.note
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class FilesystemEnrichmentDecisionStore:
    """Persist sparse user Enrichment decisions, never ResolvedEnrichment output."""

    layout: StorageLayout

    @property
    def path(self) -> Path:
        return self.layout.enrichment_decisions

    def load(self) -> tuple[EnrichmentDecision, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise EnrichmentDecisionStoreError(
                f"Unable to read Enrichment decisions {self.path}: {exc}"
            ) from exc

        decisions: list[EnrichmentDecision] = []
        seen_transaction_ids: set[str] = set()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EnrichmentDecisionStoreError(
                    f"Unable to parse Enrichment decisions {self.path} at line {line_number}: {exc.msg}"
                ) from exc
            decision = _parse_decision(raw, path=self.path, line_number=line_number)
            if decision.transaction_id in seen_transaction_ids:
                raise EnrichmentDecisionStoreError(
                    f"Duplicate EnrichmentDecision transaction_id {decision.transaction_id!r}"
                )
            seen_transaction_ids.add(decision.transaction_id)
            decisions.append(decision)
        return tuple(decisions)

    def replace(self, decisions: tuple[EnrichmentDecision, ...]) -> None:
        """Atomically replace sparse decisions after the caller validates downstream resolution."""
        seen_transaction_ids: set[str] = set()
        for decision in decisions:
            if decision.transaction_id in seen_transaction_ids:
                raise EnrichmentDecisionStoreError(
                    f"Duplicate EnrichmentDecision transaction_id {decision.transaction_id!r}"
                )
            seen_transaction_ids.add(decision.transaction_id)

        if not decisions:
            self.path.unlink(missing_ok=True)
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(f"{_encode_decision(decision)}\n" for decision in decisions)
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
            raise EnrichmentDecisionStoreError(
                f"Unable to persist Enrichment decisions {self.path}: {exc}"
            ) from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
