from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from family_spending.enrichment import (
    INCOME_DEFAULT_CATEGORY,
    UNCLASSIFIED_CATEGORY,
    CategorySource,
    TransactionEnrichmentState,
)

ENRICHMENT_STATE_FILE = Path("data/enrichment_state.jsonl")
_CATEGORY_SOURCES: frozenset[str] = frozenset(
    (
        "merchant_default",
        "transaction_override",
        "manual_override",
        "income_default",
        "unclassified",
    )
)


class EnrichmentStateStoreError(RuntimeError):
    """Raised when persisted current Enrichment state is malformed or internally inconsistent."""


def _optional_text(value: object, field: str, *, path: Path, line_number: int) -> str | None:
    """Normalize optional persisted text while rejecting non-string values at the storage boundary."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnrichmentStateStoreError(
            f"Invalid {field} in {path} at line {line_number}: expected string or null, got {value!r}"
        )
    stripped = value.strip()
    return stripped or None


def _validate_state(state: TransactionEnrichmentState, *, context: str) -> None:
    """Protect the small persistence contract before domain code relies on category-source semantics."""
    if not state.transaction_id.strip():
        raise EnrichmentStateStoreError(f"Invalid transaction_id {context}")
    if not state.category.strip():
        raise EnrichmentStateStoreError(f"Invalid category {context}")
    if state.category_source not in _CATEGORY_SOURCES:
        raise EnrichmentStateStoreError(
            f"Invalid category_source {state.category_source!r} {context}"
        )
    if state.category_source == "income_default":
        if state.merchant_name is not None or state.default_category is not None:
            raise EnrichmentStateStoreError(
                f"income_default state must not retain Merchant/default Category {context}"
            )
        if state.category != INCOME_DEFAULT_CATEGORY:
            raise EnrichmentStateStoreError(
                f"income_default state must use category {INCOME_DEFAULT_CATEGORY!r} {context}"
            )
        return
    if state.default_category is not None and state.merchant_name is None:
        raise EnrichmentStateStoreError(
            f"default_category requires merchant_name {context}"
        )
    if state.category_source == "merchant_default":
        if state.merchant_name is None or state.default_category is None:
            raise EnrichmentStateStoreError(
                f"merchant_default state requires merchant_name and default_category {context}"
            )
        if state.category != state.default_category:
            raise EnrichmentStateStoreError(
                f"merchant_default category must equal default_category {context}"
            )
    if state.category_source == "unclassified":
        if state.category != UNCLASSIFIED_CATEGORY:
            raise EnrichmentStateStoreError(
                f"unclassified state must use category {UNCLASSIFIED_CATEGORY!r} {context}"
            )
        if state.default_category is not None:
            raise EnrichmentStateStoreError(
                f"unclassified state must not retain a default_category {context}"
            )
    elif state.category == UNCLASSIFIED_CATEGORY:
        raise EnrichmentStateStoreError(
            f"category {UNCLASSIFIED_CATEGORY!r} requires category_source='unclassified' {context}"
        )


def _parse_state(raw: object, *, path: Path, line_number: int) -> TransactionEnrichmentState:
    """Parse one exact JSONL record so accidental schema drift fails visibly instead of being ignored."""
    expected_fields = {
        "transaction_id",
        "merchant_name",
        "default_category",
        "category",
        "category_source",
        "note",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise EnrichmentStateStoreError(
            f"Invalid Enrichment state in {path} at line {line_number}: {raw!r}"
        )
    transaction_id = raw["transaction_id"]
    category = raw["category"]
    category_source = raw["category_source"]
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise EnrichmentStateStoreError(
            f"Invalid transaction_id in {path} at line {line_number}: {transaction_id!r}"
        )
    if not isinstance(category, str) or not category.strip():
        raise EnrichmentStateStoreError(
            f"Invalid category in {path} at line {line_number}: {category!r}"
        )
    if not isinstance(category_source, str) or category_source not in _CATEGORY_SOURCES:
        raise EnrichmentStateStoreError(
            f"Invalid category_source in {path} at line {line_number}: {category_source!r}"
        )
    state = TransactionEnrichmentState(
        transaction_id=transaction_id,
        merchant_name=_optional_text(
            raw["merchant_name"], "merchant_name", path=path, line_number=line_number
        ),
        default_category=_optional_text(
            raw["default_category"], "default_category", path=path, line_number=line_number
        ),
        category=category,
        category_source=category_source,  # type: ignore[arg-type]
        note=_optional_text(raw["note"], "note", path=path, line_number=line_number),
    )
    _validate_state(state, context=f"in {path} at line {line_number}")
    return state


def read_enrichment_states(
    path: Path = ENRICHMENT_STATE_FILE,
) -> tuple[TransactionEnrichmentState, ...]:
    """Treat a missing file as first-run state so the existing source pipeline can bootstrap Enrichment."""
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EnrichmentStateStoreError(f"Unable to read Enrichment state {path}: {exc}") from exc
    states: list[TransactionEnrichmentState] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EnrichmentStateStoreError(
                f"Unable to parse Enrichment state {path} at line {line_number}: {exc.msg}"
            ) from exc
        state = _parse_state(raw, path=path, line_number=line_number)
        if state.transaction_id in seen_ids:
            raise EnrichmentStateStoreError(
                f"Duplicate transaction_id {state.transaction_id!r} in {path} at line {line_number}"
            )
        seen_ids.add(state.transaction_id)
        states.append(state)
    return tuple(states)


def _encode_state(state: TransactionEnrichmentState) -> str:
    """Keep local state deterministic so edits and recovery remain easy to inspect."""
    payload = {
        "transaction_id": state.transaction_id,
        "merchant_name": state.merchant_name,
        "default_category": state.default_category,
        "category": state.category,
        "category_source": state.category_source,
        "note": state.note,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def write_enrichment_states(
    states: tuple[TransactionEnrichmentState, ...],
    path: Path = ENRICHMENT_STATE_FILE,
) -> None:
    """Atomically replace current Enrichment state after the caller has validated the full downstream result."""
    seen_ids: set[str] = set()
    for state in states:
        _validate_state(state, context="before write")
        if state.transaction_id in seen_ids:
            raise EnrichmentStateStoreError(
                f"Enrichment states contain duplicate transaction_id {state.transaction_id!r}"
            )
        seen_ids.add(state.transaction_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{_encode_state(state)}\n" for state in states)
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
