from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from family_spending.manual_source import MANUAL_CURRENCY
from family_spending.source_records import TransactionType

SCHEDULED_INPUT_RULES_FILE = Path("data/scheduled_input_rules.json")


class ScheduledInputError(RuntimeError):
    """Raised when Scheduled Input configuration or persisted state is invalid."""


class ScheduledInputRollbackError(ScheduledInputError):
    """Raised when a runtime-owned scheduled job cannot restore its commit boundary."""


@dataclass(frozen=True)
class ScheduledInputRule:
    id: str
    enabled: bool
    transaction_type: TransactionType
    amount: Decimal
    description: str
    next_date: date
    currency: str = MANUAL_CURRENCY
    note: str | None = None
    last_occurrence_date: date | None = None
    last_source_record_id: str | None = None
    last_transaction_id: str | None = None
    last_action: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Expose the orchestration rule without pretending it is a Source Record."""
        return {
            "id": self.id,
            "enabled": self.enabled,
            "type": self.transaction_type,
            "amount": format(self.amount, "f"),
            "currency": self.currency,
            "description": self.description,
            "note": self.note,
            "next_date": self.next_date.isoformat(),
            "last_occurrence_date": (
                self.last_occurrence_date.isoformat()
                if self.last_occurrence_date is not None
                else None
            ),
            "last_source_record_id": self.last_source_record_id,
            "last_transaction_id": self.last_transaction_id,
            "last_action": self.last_action,
        }


@dataclass(frozen=True)
class ScheduledInputOccurrence:
    rule_id: str
    occurrence_date: date
    source_record_id: str
    transaction_id: str
    action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "occurrence_date": self.occurrence_date.isoformat(),
            "source_record_id": self.source_record_id,
            "transaction_id": self.transaction_id,
            "action": self.action,
        }


@dataclass(frozen=True)
class ScheduledInputRunResult:
    occurrences: tuple[ScheduledInputOccurrence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_count": len(self.occurrences),
            "occurrences": [item.to_dict() for item in self.occurrences],
        }


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ScheduledInputError(f"Scheduled Input {field} must be a string or null")
    stripped = value.strip()
    return stripped or None


def _validate_rule(rule: ScheduledInputRule) -> None:
    """Keep V1 monthly scheduling deterministic by limiting recurrence days to 1-28."""
    if not rule.id.strip():
        raise ScheduledInputError("Scheduled Input rule id must not be empty")
    if not isinstance(rule.enabled, bool):
        raise ScheduledInputError("Scheduled Input enabled must be a boolean")
    if rule.transaction_type not in ("income", "expense"):
        raise ScheduledInputError("Scheduled Input type must be 'income' or 'expense'")
    if not rule.amount.is_finite():
        raise ScheduledInputError("Scheduled Input amount must be finite")
    if not rule.currency.strip():
        raise ScheduledInputError("Scheduled Input currency must not be empty")
    if not rule.description.strip():
        raise ScheduledInputError("Scheduled Input description must not be empty")
    if rule.next_date.day > 28:
        raise ScheduledInputError(
            "Scheduled Input V1 only supports monthly occurrence days 1-28"
        )
    if (
        rule.last_occurrence_date is not None
        and rule.next_date <= rule.last_occurrence_date
    ):
        raise ScheduledInputError(
            "Scheduled Input next_date must be after the last generated occurrence"
        )
    if rule.last_action is not None and rule.last_action not in {
        "created",
        "matched",
        "reused",
        "recovered",
    }:
        raise ScheduledInputError(
            f"Scheduled Input last_action is invalid: {rule.last_action!r}"
        )
    last_fields = (
        rule.last_occurrence_date,
        rule.last_source_record_id,
        rule.last_transaction_id,
        rule.last_action,
    )
    if any(value is None for value in last_fields) and any(
        value is not None for value in last_fields
    ):
        raise ScheduledInputError(
            "Scheduled Input last execution metadata must be all present or all absent"
        )


def create_scheduled_input_rule(
    *,
    transaction_type: TransactionType,
    amount: Decimal,
    description: str,
    next_date: date,
    note: str | None = None,
    enabled: bool = True,
    currency: str = MANUAL_CURRENCY,
    rule_id: str | None = None,
) -> ScheduledInputRule:
    """Create one monthly rule; due execution belongs to the backend job runner."""
    rule = ScheduledInputRule(
        id=rule_id or f"schedule_{uuid.uuid4().hex}",
        enabled=enabled,
        transaction_type=transaction_type,
        amount=amount,
        currency=currency.strip().upper(),
        description=description.strip(),
        note=_optional_text(note, "note"),
        next_date=next_date,
    )
    _validate_rule(rule)
    return rule


def update_scheduled_input_rule(
    rule: ScheduledInputRule,
    *,
    transaction_type: TransactionType,
    amount: Decimal,
    description: str,
    next_date: date,
    note: str | None,
    enabled: bool,
) -> ScheduledInputRule:
    """Update only future rule configuration; historical occurrences stay untouched."""
    updated = replace(
        rule,
        enabled=enabled,
        transaction_type=transaction_type,
        amount=amount,
        description=description.strip(),
        note=_optional_text(note, "note"),
        next_date=next_date,
    )
    _validate_rule(updated)
    return updated


def _parse_rule(raw: object, *, path: Path, index: int) -> ScheduledInputRule:
    if not isinstance(raw, dict):
        raise ScheduledInputError(
            f"Invalid Scheduled Input rule in {path} at index {index}: expected object"
        )
    allowed = {
        "id",
        "enabled",
        "type",
        "amount",
        "currency",
        "description",
        "note",
        "next_date",
        "last_occurrence_date",
        "last_source_record_id",
        "last_transaction_id",
        "last_action",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ScheduledInputError(
            f"Invalid Scheduled Input rule in {path} at index {index}: unknown fields {unknown!r}"
        )
    try:
        rule_id = raw["id"]
        enabled = raw["enabled"]
        transaction_type = raw["type"]
        amount_raw = raw["amount"]
        currency = raw.get("currency", MANUAL_CURRENCY)
        description = raw["description"]
        next_date_raw = raw["next_date"]
    except KeyError as exc:
        raise ScheduledInputError(
            f"Invalid Scheduled Input rule in {path} at index {index}: missing {exc.args[0]!r}"
        ) from exc
    if not isinstance(rule_id, str):
        raise ScheduledInputError("Scheduled Input rule id must be a string")
    if not isinstance(enabled, bool):
        raise ScheduledInputError("Scheduled Input enabled must be a boolean")
    if transaction_type not in ("income", "expense"):
        raise ScheduledInputError("Scheduled Input type must be 'income' or 'expense'")
    if not isinstance(amount_raw, str):
        raise ScheduledInputError("Scheduled Input amount must be a decimal string")
    try:
        amount = Decimal(amount_raw)
    except InvalidOperation as exc:
        raise ScheduledInputError(
            f"Invalid Scheduled Input amount {amount_raw!r}"
        ) from exc
    if not amount.is_finite():
        raise ScheduledInputError("Scheduled Input amount must be finite")
    if not isinstance(currency, str):
        raise ScheduledInputError("Scheduled Input currency must be a string")
    if not isinstance(description, str):
        raise ScheduledInputError("Scheduled Input description must be a string")
    if not isinstance(next_date_raw, str):
        raise ScheduledInputError("Scheduled Input next_date must be a string")
    try:
        next_date = date.fromisoformat(next_date_raw)
    except ValueError as exc:
        raise ScheduledInputError(
            f"Invalid Scheduled Input next_date {next_date_raw!r}"
        ) from exc

    last_occurrence_raw = raw.get("last_occurrence_date")
    if last_occurrence_raw is None:
        last_occurrence_date = None
    elif isinstance(last_occurrence_raw, str):
        try:
            last_occurrence_date = date.fromisoformat(last_occurrence_raw)
        except ValueError as exc:
            raise ScheduledInputError(
                f"Invalid Scheduled Input last_occurrence_date {last_occurrence_raw!r}"
            ) from exc
    else:
        raise ScheduledInputError(
            "Scheduled Input last_occurrence_date must be a string or null"
        )

    rule = ScheduledInputRule(
        id=rule_id,
        enabled=enabled,
        transaction_type=transaction_type,
        amount=amount,
        currency=currency,
        description=description,
        note=_optional_text(raw.get("note"), "note"),
        next_date=next_date,
        last_occurrence_date=last_occurrence_date,
        last_source_record_id=_optional_text(
            raw.get("last_source_record_id"), "last_source_record_id"
        ),
        last_transaction_id=_optional_text(
            raw.get("last_transaction_id"), "last_transaction_id"
        ),
        last_action=_optional_text(raw.get("last_action"), "last_action"),
    )
    _validate_rule(rule)
    return rule


def read_scheduled_input_rules(
    path: Path = SCHEDULED_INPUT_RULES_FILE,
) -> tuple[ScheduledInputRule, ...]:
    """Treat a missing orchestration file as no configured schedules."""
    if not path.exists():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScheduledInputError(
            f"Unable to read Scheduled Input rules from {path}: {exc}"
        ) from exc
    if not isinstance(raw, list):
        raise ScheduledInputError(
            f"Scheduled Input rules file {path} must contain a JSON array"
        )
    rules = tuple(
        _parse_rule(item, path=path, index=index)
        for index, item in enumerate(raw)
    )
    ids = [rule.id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ScheduledInputError(
            f"Scheduled Input rules file {path} contains duplicate ids"
        )
    return rules


def write_scheduled_input_rules(
    rules: tuple[ScheduledInputRule, ...],
    path: Path = SCHEDULED_INPUT_RULES_FILE,
) -> None:
    """Atomically persist orchestration state and remove the file when no rules remain."""
    for rule in rules:
        _validate_rule(rule)
    ids = [rule.id for rule in rules]
    if len(ids) != len(set(ids)):
        raise ScheduledInputError("Scheduled Input rules contain duplicate ids")
    if not rules:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [rule.to_dict() for rule in rules]
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def next_monthly_date(value: date) -> date:
    """Advance one calendar month while preserving the V1-safe day 1-28."""
    if value.day > 28:
        raise ScheduledInputError("Scheduled Input V1 recurrence day must be 1-28")
    if value.month == 12:
        return date(value.year + 1, 1, value.day)
    return date(value.year, value.month + 1, value.day)


def occurrence_source_record_id(rule_id: str, occurrence_date: date) -> str:
    """Derive stable Source identity so a crash cannot duplicate one scheduled occurrence."""
    digest = hashlib.sha256(rule_id.encode("utf-8")).hexdigest()[:16]
    return f"manual_schedule_{digest}_{occurrence_date.strftime('%Y%m%d')}"
