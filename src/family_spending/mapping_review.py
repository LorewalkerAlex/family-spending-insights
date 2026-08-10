from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from family_spending.enrichment import (
    TransactionEnrichmentState,
    update_merchant_enrichment_state,
)
from family_spending.mapping import MerchantMappings
from family_spending.source_records import SourceRecord
from family_spending.transactions import Transaction


class MappingReviewError(ValueError):
    """Raised when a Mapping Review command is invalid for the current reviewed state."""


@dataclass(frozen=True)
class MappingReviewItem:
    description: str
    transaction_count: int
    total_amount: Decimal
    currency: str
    latest_date: date
    source_types: tuple[str, ...]
    transaction_only_exception_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "transaction_count": self.transaction_count,
            "total_amount": format(self.total_amount, "f"),
            "currency": self.currency,
            "latest_date": self.latest_date.isoformat(),
            "source_types": list(self.source_types),
            "transaction_only_exception_count": self.transaction_only_exception_count,
        }


@dataclass(frozen=True)
class MerchantMappingOption:
    name: str
    default_category: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "default_category": self.default_category}


@dataclass(frozen=True)
class MappingReviewPreview:
    token: str
    description: str
    merchant: str
    category: str
    is_new_merchant: bool
    previous_default_category: str | None
    description_transaction_count: int
    description_affected_transaction_count: int
    default_category_affected_transaction_count: int
    total_affected_transaction_count: int
    preserved_merchant_exception_count: int
    preserved_category_exception_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "description": self.description,
            "merchant": self.merchant,
            "category": self.category,
            "is_new_merchant": self.is_new_merchant,
            "previous_default_category": self.previous_default_category,
            "description_transaction_count": self.description_transaction_count,
            "description_affected_transaction_count": self.description_affected_transaction_count,
            "default_category_affected_transaction_count": self.default_category_affected_transaction_count,
            "total_affected_transaction_count": self.total_affected_transaction_count,
            "preserved_merchant_exception_count": self.preserved_merchant_exception_count,
            "preserved_category_exception_count": self.preserved_category_exception_count,
        }


@dataclass(frozen=True)
class MappingReviewPlan:
    preview: MappingReviewPreview
    enrichment_states: tuple[TransactionEnrichmentState, ...]


def build_mapping_review_items(
    transactions: tuple[Transaction, ...],
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]],
    enrichment_states_by_transaction_id: Mapping[str, TransactionEnrichmentState],
    mappings: MerchantMappings,
) -> tuple[MappingReviewItem, ...]:
    """Aggregate only unmapped source descriptions so CMB and Manual inputs share one review queue."""
    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in transactions:
        source = source_records_by_transaction_id[transaction.id]
        description = source.description
        if description is None or description in mappings.description_to_merchant:
            continue
        grouped[description].append(transaction)

    items: list[MappingReviewItem] = []
    for description, grouped_transactions in grouped.items():
        currencies = {transaction.currency for transaction in grouped_transactions}
        if len(currencies) != 1:
            raise MappingReviewError(
                f"Unmapped description {description!r} spans multiple currencies and cannot be reviewed as one group"
            )
        source_types = sorted(
            {
                source_records_by_transaction_id[transaction.id].source_type
                for transaction in grouped_transactions
            }
        )
        transaction_only_exception_count = sum(
            enrichment_states_by_transaction_id[transaction.id].merchant_name is not None
            for transaction in grouped_transactions
        )
        items.append(
            MappingReviewItem(
                description=description,
                transaction_count=len(grouped_transactions),
                total_amount=sum(
                    (transaction.amount for transaction in grouped_transactions),
                    start=Decimal("0"),
                ),
                currency=next(iter(currencies)),
                latest_date=max(
                    transaction.transaction_date for transaction in grouped_transactions
                ),
                source_types=tuple(source_types),
                transaction_only_exception_count=transaction_only_exception_count,
            )
        )
    return tuple(sorted(items, key=lambda item: (item.latest_date, item.description), reverse=True))


def build_merchant_mapping_options(
    mappings: MerchantMappings,
) -> tuple[MerchantMappingOption, ...]:
    """Expose canonical Merchant names with their current reviewed default Category."""
    return tuple(
        MerchantMappingOption(name=name, default_category=mappings.merchant_to_category[name])
        for name in sorted(mappings.merchant_to_category)
    )


def plan_mapping_review(
    *,
    transactions: tuple[Transaction, ...],
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]],
    enrichment_states: tuple[TransactionEnrichmentState, ...],
    mappings: MerchantMappings,
    description: str,
    merchant: str,
    category: str,
) -> MappingReviewPlan:
    """Plan Mapping propagation from current state without mutating files or rerunning reconciliation."""
    if description in mappings.description_to_merchant:
        raise MappingReviewError(
            f"Description {description!r} is already mapped; refresh Mapping Review before applying"
        )
    if category not in mappings.categories:
        raise MappingReviewError(f"Unknown category {category!r}")

    matching_transaction_ids = {
        transaction.id
        for transaction in transactions
        if source_records_by_transaction_id[transaction.id].description == description
    }
    if not matching_transaction_ids:
        raise MappingReviewError(
            f"Description {description!r} does not exist in the current Transaction snapshot"
        )

    previous_default_category = mappings.merchant_to_category.get(merchant)
    is_new_merchant = previous_default_category is None
    if not is_new_merchant and previous_default_category != category:
        previous_category_members = sum(
            mapped_category == previous_default_category
            for mapped_category in mappings.merchant_to_category.values()
        )
        if previous_category_members <= 1:
            raise MappingReviewError(
                f"Cannot move Merchant {merchant!r} out of Category {previous_default_category!r} "
                "because Mapping files require every formal Category to keep at least one Merchant"
            )

    state_by_id = {state.transaction_id: state for state in enrichment_states}
    next_states: list[TransactionEnrichmentState] = []
    description_affected_ids: set[str] = set()
    category_affected_ids: set[str] = set()
    preserved_merchant_exception_count = 0
    preserved_category_exception_ids: set[str] = set()

    category_mapping_changes = is_new_merchant or previous_default_category != category
    for transaction in transactions:
        state = state_by_id[transaction.id]
        source = source_records_by_transaction_id[transaction.id]
        updated = state

        follows_description_mapping = (
            source.description == description and state.merchant_name is None
        )
        if source.description == description and not follows_description_mapping:
            preserved_merchant_exception_count += 1

        if follows_description_mapping:
            updated = update_merchant_enrichment_state(
                updated,
                merchant_name=merchant,
                default_category=category,
            )
            if updated != state:
                description_affected_ids.add(transaction.id)
        elif (
            category_mapping_changes
            and state.merchant_name == merchant
            and state.default_category == previous_default_category
        ):
            updated = update_merchant_enrichment_state(
                updated,
                merchant_name=merchant,
                default_category=category,
            )
            if updated != state:
                category_affected_ids.add(transaction.id)

        if (
            updated != state
            and state.category_source in ("transaction_override", "manual_override")
            and updated.category == state.category
        ):
            preserved_category_exception_ids.add(transaction.id)
        next_states.append(updated)

    changed_ids = description_affected_ids | category_affected_ids
    token = _build_preview_token(
        description=description,
        merchant=merchant,
        category=category,
        previous_default_category=previous_default_category,
        matching_transaction_ids=matching_transaction_ids,
        enrichment_states=enrichment_states,
        changed_ids=changed_ids,
    )
    preview = MappingReviewPreview(
        token=token,
        description=description,
        merchant=merchant,
        category=category,
        is_new_merchant=is_new_merchant,
        previous_default_category=previous_default_category,
        description_transaction_count=len(matching_transaction_ids),
        description_affected_transaction_count=len(description_affected_ids),
        default_category_affected_transaction_count=len(category_affected_ids),
        total_affected_transaction_count=len(changed_ids),
        preserved_merchant_exception_count=preserved_merchant_exception_count,
        preserved_category_exception_count=len(preserved_category_exception_ids),
    )
    return MappingReviewPlan(preview=preview, enrichment_states=tuple(next_states))


def write_mapping_review(
    *,
    merchants_path: Path,
    categories_path: Path,
    description: str,
    merchant: str,
    category: str,
) -> None:
    """Persist one reviewed description path while preserving existing Mapping order and list semantics."""
    merchants = _load_yaml_document(merchants_path)
    categories = _load_yaml_document(categories_path)

    if any(description in descriptions for descriptions in merchants.values()):
        raise MappingReviewError(
            f"Description {description!r} is already present in Merchant Mapping"
        )

    if merchant in merchants:
        merchants[merchant].append(description)
    else:
        merchants[merchant] = [description]

    existing_category = next(
        (
            category_name
            for category_name, merchant_names in categories.items()
            if merchant in merchant_names
        ),
        None,
    )
    if existing_category is not None and existing_category != category:
        existing_members = categories[existing_category]
        if len(existing_members) <= 1:
            raise MappingReviewError(
                f"Cannot empty formal Category {existing_category!r} while moving Merchant {merchant!r}"
            )
        existing_members.remove(merchant)
    if category not in categories:
        raise MappingReviewError(f"Unknown category {category!r}")
    if merchant not in categories[category]:
        categories[category].append(merchant)

    _write_yaml_document(merchants_path, merchants)
    _write_yaml_document(categories_path, categories)


def _build_preview_token(
    *,
    description: str,
    merchant: str,
    category: str,
    previous_default_category: str | None,
    matching_transaction_ids: set[str],
    enrichment_states: tuple[TransactionEnrichmentState, ...],
    changed_ids: set[str],
) -> str:
    """Bind Apply to the exact state that was previewed so a stale UI cannot silently widen impact."""
    state_payload = [
        {
            "transaction_id": state.transaction_id,
            "merchant_name": state.merchant_name,
            "default_category": state.default_category,
            "category": state.category,
            "category_source": state.category_source,
        }
        for state in enrichment_states
        if state.transaction_id in matching_transaction_ids or state.transaction_id in changed_ids
    ]
    payload = {
        "description": description,
        "merchant": merchant,
        "category": category,
        "previous_default_category": previous_default_category,
        "matching_transaction_ids": sorted(matching_transaction_ids),
        "states": state_payload,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _load_yaml_document(path: Path) -> dict[str, list[str]]:
    """Load the simple reviewed YAML shape used by both Mapping files before a targeted rewrite."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MappingReviewError(f"Unable to load Mapping file {path}: {exc}") from exc
    if not isinstance(value, dict) or not value:
        raise MappingReviewError(f"Mapping file {path} must contain a non-empty YAML mapping")
    normalized: dict[str, list[str]] = {}
    for key, raw_items in value.items():
        if not isinstance(key, str) or not key.strip():
            raise MappingReviewError(f"Mapping file {path} contains an invalid key {key!r}")
        if not isinstance(raw_items, list) or not raw_items:
            raise MappingReviewError(f"Mapping entry {key!r} in {path} must contain a non-empty list")
        items: list[str] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, str) or not raw_item.strip():
                raise MappingReviewError(
                    f"Mapping entry {key!r} in {path} contains invalid value {raw_item!r}"
                )
            items.append(raw_item)
        normalized[key] = items
    return normalized


class _IndentedSafeDumper(yaml.SafeDumper):
    """Match the repository's existing two-space indentation for block sequence values."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _write_yaml_document(path: Path, mapping: dict[str, list[str]]) -> None:
    """Atomically replace one Mapping YAML file so partial bytes are never observable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.dump(
        mapping,
        Dumper=_IndentedSafeDumper,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        indent=2,
        width=4096,
    )
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            raise