from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.scheduling import ScheduleExecutionState, ScheduledRule
from family_spending.persistence.filesystem.layout import StorageLayout


class ScheduleStoreError(RuntimeError):
    """Raised when Scheduled Rule or execution cursor state is malformed."""


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        temp_path = None
    except OSError as exc:
        raise ScheduleStoreError(f"Unable to persist schedule state {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _read_array(path: Path) -> list[object]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduleStoreError(f"Unable to read schedule state {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ScheduleStoreError(f"Schedule state {path} must contain a JSON array")
    return raw


def _rule(raw: object, *, path: Path, index: int) -> ScheduledRule:
    if not isinstance(raw, dict):
        raise ScheduleStoreError(f"Scheduled Rule in {path} at index {index} must be an object")
    allowed = {
        "id",
        "enabled",
        "type",
        "amount",
        "currency",
        "description",
        "first_occurrence_date",
        "note",
    }
    if set(raw) != allowed:
        raise ScheduleStoreError(
            f"Invalid Scheduled Rule fields in {path} at index {index}: {sorted(raw)!r}"
        )
    amount_raw = raw["amount"]
    if not isinstance(amount_raw, str):
        raise ScheduleStoreError("Scheduled Rule amount must be a decimal string")
    try:
        amount = Decimal(amount_raw)
        occurrence = date.fromisoformat(raw["first_occurrence_date"])
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ScheduleStoreError(f"Invalid Scheduled Rule in {path} at index {index}: {exc}") from exc
    try:
        return ScheduledRule(
            id=raw["id"],
            enabled=raw["enabled"],
            transaction_type=raw["type"],
            amount=amount,
            description=raw["description"],
            first_occurrence_date=occurrence,
            currency=raw["currency"],
            note=raw["note"],
        )
    except (DomainInvariantError, TypeError) as exc:
        raise ScheduleStoreError(f"Invalid Scheduled Rule in {path} at index {index}: {exc}") from exc


def _execution(raw: object, *, path: Path, index: int) -> ScheduleExecutionState:
    expected = {
        "rule_id",
        "last_processed_occurrence_date",
        "last_source_record_id",
        "last_transaction_id",
        "last_action",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        fields = sorted(raw) if isinstance(raw, dict) else type(raw).__name__
        raise ScheduleStoreError(
            f"Invalid Schedule execution fields in {path} at index {index}: {fields!r}"
        )
    date_raw = raw["last_processed_occurrence_date"]
    if date_raw is None:
        processed = None
    elif isinstance(date_raw, str):
        try:
            processed = date.fromisoformat(date_raw)
        except ValueError as exc:
            raise ScheduleStoreError(
                f"Invalid Schedule execution date in {path} at index {index}: {date_raw!r}"
            ) from exc
    else:
        raise ScheduleStoreError("Schedule execution date must be a string or null")
    try:
        return ScheduleExecutionState(
            rule_id=raw["rule_id"],
            last_processed_occurrence_date=processed,
            last_source_record_id=raw["last_source_record_id"],
            last_transaction_id=raw["last_transaction_id"],
            last_action=raw["last_action"],
        )
    except (DomainInvariantError, TypeError) as exc:
        raise ScheduleStoreError(
            f"Invalid Schedule execution in {path} at index {index}: {exc}"
        ) from exc


@dataclass(frozen=True)
class FilesystemScheduleStore:
    """Persist schedule configuration separately from recoverable execution cursors."""

    layout: StorageLayout

    def load_rules(self) -> tuple[ScheduledRule, ...]:
        rules = tuple(
            _rule(item, path=self.layout.scheduled_rules, index=index)
            for index, item in enumerate(_read_array(self.layout.scheduled_rules))
        )
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ScheduleStoreError("Scheduled Rules contain duplicate ids")
        return rules

    def replace_rules(self, rules: tuple[ScheduledRule, ...]) -> None:
        ids = [rule.id for rule in rules]
        if len(ids) != len(set(ids)):
            raise ScheduleStoreError("Scheduled Rules contain duplicate ids")
        if not rules:
            self.layout.scheduled_rules.unlink(missing_ok=True)
            return
        _atomic_json(
            self.layout.scheduled_rules,
            [
                {
                    "id": rule.id,
                    "enabled": rule.enabled,
                    "type": rule.transaction_type,
                    "amount": format(rule.amount, "f"),
                    "currency": rule.currency,
                    "description": rule.description,
                    "first_occurrence_date": rule.first_occurrence_date.isoformat(),
                    "note": rule.note,
                }
                for rule in rules
            ],
        )

    def load_execution(self) -> tuple[ScheduleExecutionState, ...]:
        states = tuple(
            _execution(item, path=self.layout.schedule_execution, index=index)
            for index, item in enumerate(_read_array(self.layout.schedule_execution))
        )
        ids = [state.rule_id for state in states]
        if len(ids) != len(set(ids)):
            raise ScheduleStoreError("Schedule execution contains duplicate rule ids")
        return states

    def replace_execution(self, states: tuple[ScheduleExecutionState, ...]) -> None:
        ids = [state.rule_id for state in states]
        if len(ids) != len(set(ids)):
            raise ScheduleStoreError("Schedule execution contains duplicate rule ids")
        if not states:
            self.layout.schedule_execution.unlink(missing_ok=True)
            return
        _atomic_json(
            self.layout.schedule_execution,
            [
                {
                    "rule_id": state.rule_id,
                    "last_processed_occurrence_date": (
                        state.last_processed_occurrence_date.isoformat()
                        if state.last_processed_occurrence_date is not None
                        else None
                    ),
                    "last_source_record_id": state.last_source_record_id,
                    "last_transaction_id": state.last_transaction_id,
                    "last_action": state.last_action,
                }
                for state in states
            ],
        )
