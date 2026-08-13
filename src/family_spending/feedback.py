from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

FEEDBACK_FILE = Path("data/feedback.jsonl")
FEEDBACK_STATUSES = frozenset({"open", "resolved"})
FEEDBACK_RUNTIMES = frozenset({"desktop_web", "mini_h5", "weapp"})

FeedbackStatus = Literal["open", "resolved"]
FeedbackRuntime = Literal["desktop_web", "mini_h5", "weapp"]


class FeedbackError(RuntimeError):
    """Raised when persisted Feedback or a Feedback state transition is invalid."""


@dataclass(frozen=True)
class FeedbackContext:
    runtime: FeedbackRuntime | None = None
    page: str | None = None
    workspace: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Serialize only available context so capture remains lightweight across runtimes."""
        values = {
            "runtime": self.runtime,
            "page": self.page,
            "workspace": self.workspace,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class FeedbackItem:
    id: str
    created_at: datetime
    status: FeedbackStatus
    content: str
    context: FeedbackContext

    def to_dict(self) -> dict[str, object]:
        """Expose the stable Feedback V1 contract without development-tracker metadata."""
        return {
            "id": self.id,
            "created_at": _format_timestamp(self.created_at),
            "status": self.status,
            "content": self.content,
            "context": self.context.to_dict(),
        }


def _required_text(value: object, field: str) -> str:
    """Normalize required persisted text while rejecting empty or non-text values."""
    if not isinstance(value, str) or value.strip() == "":
        raise FeedbackError(f"Feedback {field} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, field: str) -> str | None:
    """Normalize optional context text without silently accepting non-text values."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FeedbackError(f"Feedback context {field} must be a string or null")
    stripped = value.strip()
    return stripped or None


def _validate_context(context: FeedbackContext) -> None:
    """Keep runtime identity and optional entity references internally coherent."""
    if context.runtime is not None and context.runtime not in FEEDBACK_RUNTIMES:
        raise FeedbackError(f"Feedback runtime is invalid: {context.runtime!r}")
    if (context.entity_type is None) != (context.entity_id is None):
        raise FeedbackError(
            "Feedback entity_type and entity_id must be provided together"
        )


def _validate_item(item: FeedbackItem) -> None:
    """Validate the complete persisted record before either reading or writing it."""
    _required_text(item.id, "id")
    _required_text(item.content, "content")
    if item.status not in FEEDBACK_STATUSES:
        raise FeedbackError(f"Feedback status is invalid: {item.status!r}")
    if item.created_at.tzinfo is None or item.created_at.utcoffset() is None:
        raise FeedbackError("Feedback created_at must include a timezone")
    _validate_context(item.context)


def _format_timestamp(value: datetime) -> str:
    """Persist timestamps in canonical UTC form so clients do not infer local timezones."""
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object) -> datetime:
    """Parse one timezone-aware ISO timestamp and normalize it to UTC."""
    if not isinstance(value, str):
        raise FeedbackError("Feedback created_at must be an ISO timestamp string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise FeedbackError(f"Invalid Feedback created_at: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FeedbackError("Feedback created_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _parse_context(raw: object) -> FeedbackContext:
    """Decode the small, explicit Feedback context contract without accepting unused fields."""
    if not isinstance(raw, dict):
        raise FeedbackError("Feedback context must be an object")
    allowed = {"runtime", "page", "workspace", "entity_type", "entity_id"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise FeedbackError(f"Feedback context contains unknown fields: {unknown!r}")
    runtime = _optional_text(raw.get("runtime"), "runtime")
    if runtime is not None and runtime not in FEEDBACK_RUNTIMES:
        raise FeedbackError(f"Feedback runtime is invalid: {runtime!r}")
    context = FeedbackContext(
        runtime=runtime,
        page=_optional_text(raw.get("page"), "page"),
        workspace=_optional_text(raw.get("workspace"), "workspace"),
        entity_type=_optional_text(raw.get("entity_type"), "entity_type"),
        entity_id=_optional_text(raw.get("entity_id"), "entity_id"),
    )
    _validate_context(context)
    return context


def _parse_item(raw: object, *, path: Path, line_number: int) -> FeedbackItem:
    """Decode one JSONL record strictly so malformed local state never leaks to clients."""
    if not isinstance(raw, dict):
        raise FeedbackError(
            f"Invalid Feedback record in {path} at line {line_number}: expected object"
        )
    allowed = {"id", "created_at", "status", "content", "context"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise FeedbackError(
            f"Invalid Feedback record in {path} at line {line_number}: unknown fields {unknown!r}"
        )
    missing = sorted(allowed - set(raw))
    if missing:
        raise FeedbackError(
            f"Invalid Feedback record in {path} at line {line_number}: missing fields {missing!r}"
        )
    status = raw["status"]
    if status not in FEEDBACK_STATUSES:
        raise FeedbackError(f"Feedback status is invalid: {status!r}")
    item = FeedbackItem(
        id=_required_text(raw["id"], "id"),
        created_at=_parse_timestamp(raw["created_at"]),
        status=status,
        content=_required_text(raw["content"], "content"),
        context=_parse_context(raw["context"]),
    )
    _validate_item(item)
    return item


def create_feedback_item(
    *,
    content: str,
    context: FeedbackContext,
) -> FeedbackItem:
    """Create one local product-feedback record with stable identity and UTC creation time."""
    item = FeedbackItem(
        id=f"feedback_{uuid.uuid4().hex}",
        created_at=datetime.now(timezone.utc),
        status="open",
        content=_required_text(content, "content"),
        context=context,
    )
    _validate_item(item)
    return item


def update_feedback_status(
    item: FeedbackItem,
    status: FeedbackStatus,
) -> FeedbackItem:
    """Resolve or reopen one Feedback item without changing its original capture context."""
    if status not in FEEDBACK_STATUSES:
        raise FeedbackError(f"Feedback status is invalid: {status!r}")
    updated = replace(item, status=status)
    _validate_item(updated)
    return updated


def read_feedback_items(path: Path = FEEDBACK_FILE) -> tuple[FeedbackItem, ...]:
    """Read Feedback in persisted creation order; a missing file means an empty inbox."""
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FeedbackError(f"Unable to read Feedback from {path}: {exc}") from exc
    items: list[FeedbackItem] = []
    for line_number, line in enumerate(lines, start=1):
        if line.strip() == "":
            raise FeedbackError(
                f"Invalid Feedback record in {path} at line {line_number}: blank line"
            )
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FeedbackError(
                f"Invalid Feedback JSON in {path} at line {line_number}: {exc}"
            ) from exc
        items.append(_parse_item(raw, path=path, line_number=line_number))
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise FeedbackError(f"Feedback file {path} contains duplicate ids")
    return tuple(items)


def write_feedback_items(
    items: tuple[FeedbackItem, ...],
    path: Path = FEEDBACK_FILE,
) -> None:
    """Atomically replace the local Feedback JSONL store after validating every record."""
    for item in items:
        _validate_item(item)
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise FeedbackError("Feedback items contain duplicate ids")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        json.dumps(item.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n"
        for item in items
    )
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
