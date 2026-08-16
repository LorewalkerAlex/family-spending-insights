from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.feedback import FeedbackContext, FeedbackItem
from family_spending.persistence.filesystem.layout import StorageLayout


class FeedbackStoreError(RuntimeError):
    """Raised when canonical Feedback state is malformed or cannot be persisted."""


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(raw: object, *, path: Path, line_number: int) -> datetime:
    if not isinstance(raw, str):
        raise FeedbackStoreError(
            f"Feedback created_at in {path} at line {line_number} must be a string"
        )
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise FeedbackStoreError(
            f"Invalid Feedback created_at in {path} at line {line_number}: {raw!r}"
        ) from exc


def _optional_text(raw: object, field: str, *, path: Path, line_number: int) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise FeedbackStoreError(
            f"Feedback context {field} in {path} at line {line_number} must be a string or null"
        )
    return raw


def _parse_item(raw: object, *, path: Path, line_number: int) -> FeedbackItem:
    if not isinstance(raw, dict):
        raise FeedbackStoreError(
            f"Feedback record in {path} at line {line_number} must be an object"
        )
    allowed = {"id", "created_at", "status", "content", "context"}
    if set(raw) != allowed:
        raise FeedbackStoreError(
            f"Invalid Feedback fields in {path} at line {line_number}: {sorted(raw)!r}"
        )
    context_raw = raw["context"]
    if not isinstance(context_raw, dict):
        raise FeedbackStoreError(
            f"Feedback context in {path} at line {line_number} must be an object"
        )
    context_allowed = {"runtime", "page", "workspace", "entity_type", "entity_id"}
    unknown_context = sorted(set(context_raw) - context_allowed)
    if unknown_context:
        raise FeedbackStoreError(
            f"Unknown Feedback context fields in {path} at line {line_number}: {unknown_context!r}"
        )
    try:
        context = FeedbackContext(
            runtime=_optional_text(
                context_raw.get("runtime"), "runtime", path=path, line_number=line_number
            ),
            page=_optional_text(context_raw.get("page"), "page", path=path, line_number=line_number),
            workspace=_optional_text(
                context_raw.get("workspace"), "workspace", path=path, line_number=line_number
            ),
            entity_type=_optional_text(
                context_raw.get("entity_type"), "entity_type", path=path, line_number=line_number
            ),
            entity_id=_optional_text(
                context_raw.get("entity_id"), "entity_id", path=path, line_number=line_number
            ),
        )
        return FeedbackItem(
            id=raw["id"],
            created_at=_parse_timestamp(raw["created_at"], path=path, line_number=line_number),
            status=raw["status"],
            content=raw["content"],
            context=context,
        )
    except (DomainInvariantError, TypeError) as exc:
        raise FeedbackStoreError(
            f"Invalid Feedback record in {path} at line {line_number}: {exc}"
        ) from exc


def _encode_item(item: FeedbackItem) -> str:
    context = {
        key: value
        for key, value in {
            "runtime": item.context.runtime,
            "page": item.context.page,
            "workspace": item.context.workspace,
            "entity_type": item.context.entity_type,
            "entity_id": item.context.entity_id,
        }.items()
        if value is not None
    }
    return json.dumps(
        {
            "id": item.id,
            "created_at": _timestamp(item.created_at),
            "status": item.status,
            "content": item.content,
            "context": context,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class FilesystemFeedbackStore:
    """Persist canonical Feedback as strict JSONL in household product state."""

    layout: StorageLayout

    @property
    def path(self) -> Path:
        return self.layout.feedback

    def load(self) -> tuple[FeedbackItem, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise FeedbackStoreError(f"Unable to read Feedback {self.path}: {exc}") from exc
        items: list[FeedbackItem] = []
        seen: set[str] = set()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise FeedbackStoreError(
                    f"Blank Feedback line in {self.path} at line {line_number}"
                )
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FeedbackStoreError(
                    f"Invalid Feedback JSON in {self.path} at line {line_number}: {exc.msg}"
                ) from exc
            item = _parse_item(raw, path=self.path, line_number=line_number)
            if item.id in seen:
                raise FeedbackStoreError(f"Duplicate Feedback id {item.id!r}")
            seen.add(item.id)
            items.append(item)
        return tuple(items)

    def replace(self, items: tuple[FeedbackItem, ...]) -> None:
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise FeedbackStoreError("Feedback items contain duplicate ids")
        if not items:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(f"{_encode_item(item)}\n" for item in items)
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
            raise FeedbackStoreError(f"Unable to persist Feedback {self.path}: {exc}") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
