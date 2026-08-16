from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from family_spending.domain.feedback import FeedbackContext, FeedbackItem
from family_spending.domain.mapping import MappingCatalog, UNCLASSIFIED_CATEGORY

LEGACY_CMB_CURRENCY = "CNY"
LEGACY_MANUAL_CURRENCY = "CNY"
LEGACY_TRANSACTION_FIELDS = (
    "transaction_id",
    "transaction_date",
    "amount",
    "description",
    "source_email",
    "source_index",
)
LegacyCategorySource = Literal[
    "merchant_default",
    "transaction_override",
    "manual_override",
    "income_default",
    "unclassified",
]


class LegacySnapshotError(RuntimeError):
    """Raised when legacy household state cannot be interpreted without guessing."""


@dataclass(frozen=True)
class LegacyLayout:
    """Resolve every current-backend file from one explicit private data root."""

    data_root: Path

    def __post_init__(self) -> None:
        root = Path(self.data_root).expanduser().resolve()
        object.__setattr__(self, "data_root", root)

    @property
    def emails(self) -> Path:
        return self.data_root / "emails"

    @property
    def transactions(self) -> Path:
        return self.data_root / "transactions.csv"

    @property
    def manual_source(self) -> Path:
        return self.data_root / "manual_source_records.jsonl"

    @property
    def source_links(self) -> Path:
        return self.data_root / "transaction_source_links.jsonl"

    @property
    def enrichment_state(self) -> Path:
        return self.data_root / "enrichment_state.jsonl"

    @property
    def merchants(self) -> Path:
        return self.data_root / "mappings" / "merchants.yaml"

    @property
    def categories(self) -> Path:
        return self.data_root / "mappings" / "categories.yaml"

    @property
    def scheduled_rules(self) -> Path:
        return self.data_root / "scheduled_input_rules.json"

    @property
    def feedback(self) -> Path:
        return self.data_root / "feedback.jsonl"

    @property
    def spending_statistics(self) -> Path:
        return self.data_root / "reports" / "spending_statistics.json"

    @property
    def financial_summary(self) -> Path:
        return self.data_root / "reports" / "financial_summary.json"


@dataclass(frozen=True)
class LegacyCmbTransaction:
    source_record_id: str
    transaction_date: date
    amount: Decimal
    description: str
    source_email: str
    source_index: int


@dataclass(frozen=True)
class LegacyManualRecord:
    source_record_id: str
    transaction_type: Literal["income", "expense"]
    transaction_date: date
    amount: Decimal
    currency: str
    description: str | None
    merchant_name: str | None
    category: str | None
    note: str | None


@dataclass(frozen=True)
class LegacySourceLink:
    transaction_id: str
    source_record_id: str
    role: Literal["authoritative", "supporting"]


@dataclass(frozen=True)
class LegacyEnrichmentState:
    transaction_id: str
    merchant_name: str | None
    default_category: str | None
    category: str
    category_source: LegacyCategorySource
    note: str | None


@dataclass(frozen=True)
class LegacyScheduledRule:
    id: str
    enabled: bool
    transaction_type: Literal["income", "expense"]
    amount: Decimal
    currency: str
    description: str
    note: str | None
    next_date: date
    last_occurrence_date: date | None
    last_source_record_id: str | None
    last_transaction_id: str | None
    last_action: str | None


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Reject duplicate reviewed YAML keys instead of inheriting PyYAML overwrite behavior."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found unhashable key {key!r}",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def legacy_cmb_source_id(source_email: str, source_index: int) -> str:
    """Reproduce the current backend's historical CMB SourceRecord identity exactly."""
    payload = f"cmb\0{source_email}\0{source_index}".encode("utf-8")
    return f"cmb_{hashlib.sha256(payload).hexdigest()[:24]}"


def legacy_schedule_occurrence_source_id(rule_id: str, occurrence_date: date) -> str:
    """Reproduce the current backend's deterministic scheduled Manual Source id."""
    digest = hashlib.sha256(rule_id.encode("utf-8")).hexdigest()[:16]
    return f"manual_schedule_{digest}_{occurrence_date.strftime('%Y%m%d')}"


def _non_empty_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LegacySnapshotError(f"{label} must be non-empty text")
    return value.strip()


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LegacySnapshotError(f"{label} must be text or null")
    stripped = value.strip()
    return stripped or None


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise LegacySnapshotError(f"{label} must be a decimal string")
    try:
        amount = Decimal(value.strip())
    except InvalidOperation as exc:
        raise LegacySnapshotError(f"{label} is not a valid decimal string") from exc
    if not amount.is_finite():
        raise LegacySnapshotError(f"{label} must be finite")
    return amount


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise LegacySnapshotError(f"{label} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LegacySnapshotError(f"{label} is not a valid ISO date") from exc


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LegacySnapshotError(f"Unable to read legacy JSON {path}: {exc}") from exc


def _read_json_lines(path: Path, *, missing_is_empty: bool = True) -> list[tuple[int, object]]:
    if not path.exists():
        if missing_is_empty:
            return []
        raise LegacySnapshotError(f"Legacy file is missing: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LegacySnapshotError(f"Unable to read legacy file {path}: {exc}") from exc
    records: list[tuple[int, object]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append((line_number, json.loads(line)))
        except json.JSONDecodeError as exc:
            raise LegacySnapshotError(
                f"Invalid JSON in {path} at line {line_number}: {exc.msg}"
            ) from exc
    return records


def load_legacy_cmb_transactions(path: Path) -> tuple[LegacyCmbTransaction, ...]:
    """Load the old CSV only as a migration audit index, never as canonical truth."""
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise LegacySnapshotError(f"Unable to read legacy transaction CSV {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        if fields != LEGACY_TRANSACTION_FIELDS:
            raise LegacySnapshotError(
                f"Legacy transaction CSV header mismatch in {path}: {fields!r}"
            )
        result: list[LegacyCmbTransaction] = []
        seen: set[str] = set()
        for row in reader:
            if row.get(None):
                raise LegacySnapshotError(
                    f"Legacy transaction CSV has extra columns at line {reader.line_num}"
                )
            source_email = _non_empty_text(row.get("source_email"), "source_email")
            source_index_text = _non_empty_text(row.get("source_index"), "source_index")
            try:
                source_index = int(source_index_text)
            except ValueError as exc:
                raise LegacySnapshotError("source_index must be a positive integer") from exc
            if source_index <= 0 or str(source_index) != source_index_text:
                raise LegacySnapshotError("source_index must be a normalized positive integer")
            source_id = _non_empty_text(row.get("transaction_id"), "transaction_id")
            expected = legacy_cmb_source_id(source_email, source_index)
            if source_id != expected:
                raise LegacySnapshotError(
                    f"Legacy CMB id formula mismatch for {source_email!r} index {source_index}"
                )
            if source_id in seen:
                raise LegacySnapshotError(f"Duplicate legacy CMB source id {source_id!r}")
            seen.add(source_id)
            result.append(
                LegacyCmbTransaction(
                    source_record_id=source_id,
                    transaction_date=_date(row.get("transaction_date"), "transaction_date"),
                    amount=_decimal(row.get("amount"), "amount"),
                    description=_non_empty_text(row.get("description"), "description"),
                    source_email=source_email,
                    source_index=source_index,
                )
            )
    return tuple(result)


def load_legacy_manual_records(path: Path) -> tuple[LegacyManualRecord, ...]:
    result: list[LegacyManualRecord] = []
    seen: set[str] = set()
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
    for line_number, raw in _read_json_lines(path):
        if not isinstance(raw, dict):
            raise LegacySnapshotError(f"Manual record in {path} line {line_number} must be an object")
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise LegacySnapshotError(
                f"Unknown Manual fields in {path} line {line_number}: {unknown!r}"
            )
        source_id = _non_empty_text(raw.get("id"), "Manual id")
        if source_id in seen:
            raise LegacySnapshotError(f"Duplicate Manual source id {source_id!r}")
        seen.add(source_id)
        transaction_type = raw.get("type")
        if transaction_type not in ("income", "expense"):
            raise LegacySnapshotError(f"Invalid Manual type in {path} line {line_number}")
        currency = _non_empty_text(raw.get("currency", LEGACY_MANUAL_CURRENCY), "Manual currency")
        result.append(
            LegacyManualRecord(
                source_record_id=source_id,
                transaction_type=transaction_type,
                transaction_date=_date(raw.get("date"), "Manual date"),
                amount=_decimal(raw.get("amount"), "Manual amount"),
                currency=currency.upper(),
                description=_optional_text(raw.get("description"), "Manual description"),
                merchant_name=_optional_text(raw.get("merchant"), "Manual merchant"),
                category=_optional_text(raw.get("category"), "Manual category"),
                note=_optional_text(raw.get("note"), "Manual note"),
            )
        )
    return tuple(result)


def load_legacy_source_links(path: Path) -> tuple[LegacySourceLink, ...]:
    result: list[LegacySourceLink] = []
    seen_sources: set[str] = set()
    authoritative: set[str] = set()
    for line_number, raw in _read_json_lines(path, missing_is_empty=False):
        if not isinstance(raw, dict) or set(raw) != {"transaction_id", "source_record_id", "role"}:
            raise LegacySnapshotError(f"Invalid SourceLink in {path} line {line_number}")
        transaction_id = _non_empty_text(raw["transaction_id"], "transaction_id")
        source_id = _non_empty_text(raw["source_record_id"], "source_record_id")
        role = raw["role"]
        if role not in ("authoritative", "supporting"):
            raise LegacySnapshotError(f"Invalid SourceLink role in {path} line {line_number}")
        if source_id in seen_sources:
            raise LegacySnapshotError(f"Legacy source {source_id!r} is linked more than once")
        seen_sources.add(source_id)
        if role == "authoritative":
            if transaction_id in authoritative:
                raise LegacySnapshotError(
                    f"Legacy Transaction {transaction_id!r} has multiple authoritative links"
                )
            authoritative.add(transaction_id)
        result.append(LegacySourceLink(transaction_id, source_id, role))
    transaction_ids = {item.transaction_id for item in result}
    missing = sorted(transaction_ids - authoritative)
    if missing:
        raise LegacySnapshotError(f"Legacy Transactions lack authority: {missing!r}")
    return tuple(result)


def load_legacy_enrichment(path: Path) -> tuple[LegacyEnrichmentState, ...]:
    expected = {
        "transaction_id",
        "merchant_name",
        "default_category",
        "category",
        "category_source",
        "note",
    }
    allowed_sources = {
        "merchant_default",
        "transaction_override",
        "manual_override",
        "income_default",
        "unclassified",
    }
    result: list[LegacyEnrichmentState] = []
    seen: set[str] = set()
    for line_number, raw in _read_json_lines(path, missing_is_empty=False):
        if not isinstance(raw, dict) or set(raw) != expected:
            raise LegacySnapshotError(f"Invalid Enrichment state in {path} line {line_number}")
        transaction_id = _non_empty_text(raw["transaction_id"], "transaction_id")
        if transaction_id in seen:
            raise LegacySnapshotError(f"Duplicate Enrichment state {transaction_id!r}")
        seen.add(transaction_id)
        category_source = raw["category_source"]
        if category_source not in allowed_sources:
            raise LegacySnapshotError(
                f"Invalid category_source in {path} line {line_number}: {category_source!r}"
            )
        result.append(
            LegacyEnrichmentState(
                transaction_id=transaction_id,
                merchant_name=_optional_text(raw["merchant_name"], "merchant_name"),
                default_category=_optional_text(raw["default_category"], "default_category"),
                category=_non_empty_text(raw["category"], "category"),
                category_source=category_source,
                note=_optional_text(raw["note"], "note"),
            )
        )
    return tuple(result)


def load_legacy_schedules(path: Path) -> tuple[LegacyScheduledRule, ...]:
    if not path.exists():
        return ()
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise LegacySnapshotError(f"Legacy schedule state {path} must contain a JSON array")
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
    result: list[LegacyScheduledRule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) - allowed:
            raise LegacySnapshotError(f"Invalid Scheduled Input rule in {path} index {index}")
        rule_id = _non_empty_text(item.get("id"), "schedule id")
        if rule_id in seen:
            raise LegacySnapshotError(f"Duplicate Scheduled Input rule {rule_id!r}")
        seen.add(rule_id)
        enabled = item.get("enabled")
        if not isinstance(enabled, bool):
            raise LegacySnapshotError("Scheduled Input enabled must be boolean")
        transaction_type = item.get("type")
        if transaction_type not in ("income", "expense"):
            raise LegacySnapshotError("Scheduled Input type is invalid")
        next_date = _date(item.get("next_date"), "Scheduled Input next_date")
        if next_date.day > 28:
            raise LegacySnapshotError("Scheduled Input recurrence day must be 1-28")
        last_date_raw = item.get("last_occurrence_date")
        last_date = None if last_date_raw is None else _date(last_date_raw, "last_occurrence_date")
        last_source = _optional_text(item.get("last_source_record_id"), "last_source_record_id")
        last_transaction = _optional_text(item.get("last_transaction_id"), "last_transaction_id")
        last_action = _optional_text(item.get("last_action"), "last_action")
        last_values = (last_date, last_source, last_transaction, last_action)
        if any(value is None for value in last_values) and any(value is not None for value in last_values):
            raise LegacySnapshotError("Scheduled Input last execution metadata is partial")
        if last_action is not None and last_action not in {"created", "matched", "reused", "recovered"}:
            raise LegacySnapshotError(f"Invalid Scheduled Input last action {last_action!r}")
        if last_date is not None and next_date <= last_date:
            raise LegacySnapshotError("Scheduled Input next_date must follow last occurrence")
        result.append(
            LegacyScheduledRule(
                id=rule_id,
                enabled=enabled,
                transaction_type=transaction_type,
                amount=_decimal(item.get("amount"), "Scheduled Input amount"),
                currency=_non_empty_text(item.get("currency", LEGACY_MANUAL_CURRENCY), "currency").upper(),
                description=_non_empty_text(item.get("description"), "description"),
                note=_optional_text(item.get("note"), "note"),
                next_date=next_date,
                last_occurrence_date=last_date,
                last_source_record_id=last_source,
                last_transaction_id=last_transaction,
                last_action=last_action,
            )
        )
    return tuple(result)


def load_legacy_feedback(path: Path) -> tuple[FeedbackItem, ...]:
    result: list[FeedbackItem] = []
    seen: set[str] = set()
    for line_number, raw in _read_json_lines(path):
        if not isinstance(raw, dict) or set(raw) != {"id", "created_at", "status", "content", "context"}:
            raise LegacySnapshotError(f"Invalid Feedback record in {path} line {line_number}")
        item_id = _non_empty_text(raw["id"], "Feedback id")
        if item_id in seen:
            raise LegacySnapshotError(f"Duplicate Feedback id {item_id!r}")
        seen.add(item_id)
        timestamp = raw["created_at"]
        if not isinstance(timestamp, str):
            raise LegacySnapshotError("Feedback created_at must be a timestamp string")
        candidate = timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        try:
            created_at = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise LegacySnapshotError(f"Invalid Feedback created_at {timestamp!r}") from exc
        context_raw = raw["context"]
        if not isinstance(context_raw, dict):
            raise LegacySnapshotError("Feedback context must be an object")
        unknown = sorted(set(context_raw) - {"runtime", "page", "workspace", "entity_type", "entity_id"})
        if unknown:
            raise LegacySnapshotError(f"Unknown Feedback context fields: {unknown!r}")
        try:
            context = FeedbackContext(
                runtime=_optional_text(context_raw.get("runtime"), "runtime"),
                page=_optional_text(context_raw.get("page"), "page"),
                workspace=_optional_text(context_raw.get("workspace"), "workspace"),
                entity_type=_optional_text(context_raw.get("entity_type"), "entity_type"),
                entity_id=_optional_text(context_raw.get("entity_id"), "entity_id"),
            )
            item = FeedbackItem(
                id=item_id,
                created_at=created_at,
                status=raw["status"],
                content=_non_empty_text(raw["content"], "Feedback content"),
                context=context,
            )
        except (TypeError, ValueError) as exc:
            raise LegacySnapshotError(f"Invalid Feedback record in {path} line {line_number}: {exc}") from exc
        result.append(item)
    return tuple(result)


def _read_yaml_mapping(path: Path) -> dict[object, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LegacySnapshotError(f"Unable to read Mapping {path}: {exc}") from exc
    try:
        raw = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise LegacySnapshotError(f"Unable to parse Mapping {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LegacySnapshotError(f"Mapping {path} must contain a YAML mapping")
    return raw


def load_legacy_mappings(merchants_path: Path, categories_path: Path) -> MappingCatalog:
    merchants_exists = merchants_path.exists()
    categories_exists = categories_path.exists()
    if not merchants_exists and not categories_exists:
        return MappingCatalog.empty()
    if merchants_exists != categories_exists:
        raise LegacySnapshotError("Legacy Mapping merchants/categories files must exist together")
    raw_merchants = _read_yaml_mapping(merchants_path)
    raw_categories = _read_yaml_mapping(categories_path)
    description_to_merchant: dict[str, str] = {}
    merchant_names: set[str] = set()
    for merchant_raw, descriptions_raw in raw_merchants.items():
        merchant = _non_empty_text(merchant_raw, "merchant")
        if not isinstance(descriptions_raw, list) or not descriptions_raw:
            raise LegacySnapshotError(f"Merchant {merchant!r} must have descriptions")
        merchant_names.add(merchant)
        for description_raw in descriptions_raw:
            description = _non_empty_text(description_raw, "description")
            if description in description_to_merchant:
                raise LegacySnapshotError(f"Description {description!r} is mapped more than once")
            description_to_merchant[description] = merchant
    merchant_to_category: dict[str, str] = {}
    categories: set[str] = set()
    for category_raw, merchants_raw in raw_categories.items():
        category = _non_empty_text(category_raw, "category")
        if category == UNCLASSIFIED_CATEGORY:
            raise LegacySnapshotError("Runtime unclassified category cannot be formal Mapping")
        if not isinstance(merchants_raw, list) or not merchants_raw:
            raise LegacySnapshotError(f"Category {category!r} must have merchants")
        categories.add(category)
        for merchant_raw in merchants_raw:
            merchant = _non_empty_text(merchant_raw, "merchant")
            if merchant in merchant_to_category:
                raise LegacySnapshotError(f"Merchant {merchant!r} is categorized more than once")
            merchant_to_category[merchant] = category
    if merchant_names != set(merchant_to_category):
        raise LegacySnapshotError("Legacy Mapping merchant sets do not match")
    try:
        return MappingCatalog(
            description_to_merchant=description_to_merchant,
            merchant_to_category=merchant_to_category,
            categories=frozenset(categories),
        )
    except Exception as exc:
        raise LegacySnapshotError(f"Invalid legacy Mapping: {exc}") from exc


def load_projection(path: Path, label: str) -> dict[str, object]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise LegacySnapshotError(f"{label} projection {path} must contain one JSON object")
    return raw
