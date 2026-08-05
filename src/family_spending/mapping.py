from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.settings import (
    CATEGORIES_FILE,
    MERCHANTS_FILE,
    TRANSACTION_CATEGORY_OVERRIDES_FILE,
)

UNCLASSIFIED_CATEGORY = "待分类"
OTHER_EXPENSE_CATEGORY = "其他支出"
GENERAL_SHOPPING_CATEGORY = "综合购物"
HIGH_VALUE_GENERAL_SHOPPING_THRESHOLD = Decimal("-1000")
OTHER_EXPENSE_REVIEW = "other_expense_review"
HIGH_VALUE_GENERAL_SHOPPING_REVIEW = "high_value_general_shopping_review"

CategorySource = Literal[
    "merchant_default",
    "transaction_override",
    "unclassified",
]


class MappingDataError(RuntimeError):
    """Raised when the official Mapping files violate their data contract."""


class MappingResolutionError(RuntimeError):
    """Raised when loaded Mapping data cannot resolve a transaction consistently."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    pass


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


@dataclass(frozen=True)
class MerchantMappings:
    description_to_merchant: Mapping[str, str]
    merchant_to_category: Mapping[str, str]
    transaction_category_overrides: Mapping[str, str]
    categories: frozenset[str]
    merchants_path: Path
    categories_path: Path
    overrides_path: Path


@dataclass(frozen=True)
class ResolvedTransaction:
    transaction: CmbTransaction
    merchant_name: str | None
    display_name: str
    default_category: str | None
    category: str
    category_source: CategorySource
    is_unmatched: bool
    review_signals: tuple[str, ...]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MappingDataError(f"Unable to read Mapping file {path}: {exc}") from exc


def _load_yaml_mapping(path: Path) -> dict[object, object]:
    try:
        value = yaml.load(_read_text(path), Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise MappingDataError(f"Unable to parse YAML Mapping file {path}: {exc}") from exc

    if not isinstance(value, dict):
        actual_type = type(value).__name__
        raise MappingDataError(f"Expected a YAML mapping in {path}, got {actual_type}")
    if not value:
        raise MappingDataError(f"Mapping file {path} must not be empty")
    return value


def _require_non_empty_string(value: object, label: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingDataError(f"Invalid {label} in {path}: expected a non-empty string, got {value!r}")
    return value


def _load_merchants(path: Path) -> tuple[set[str], dict[str, str]]:
    raw_mapping = _load_yaml_mapping(path)
    merchant_names: set[str] = set()
    description_to_merchant: dict[str, str] = {}

    for raw_merchant, raw_descriptions in raw_mapping.items():
        merchant = _require_non_empty_string(raw_merchant, "merchant name", path)
        if not isinstance(raw_descriptions, list) or not raw_descriptions:
            raise MappingDataError(
                f"Merchant {merchant!r} in {path} must have a non-empty description list"
            )

        merchant_names.add(merchant)
        for raw_description in raw_descriptions:
            description = _require_non_empty_string(raw_description, "description", path)
            existing_merchant = description_to_merchant.get(description)
            if existing_merchant is not None:
                raise MappingDataError(
                    f"Duplicate description {description!r} in {path}: already assigned to "
                    f"{existing_merchant!r}, cannot assign to {merchant!r}"
                )
            description_to_merchant[description] = merchant

    return merchant_names, description_to_merchant


def _load_categories(path: Path) -> tuple[frozenset[str], dict[str, str]]:
    raw_mapping = _load_yaml_mapping(path)
    categories: set[str] = set()
    merchant_to_category: dict[str, str] = {}

    for raw_category, raw_merchants in raw_mapping.items():
        category = _require_non_empty_string(raw_category, "category", path)
        if category == UNCLASSIFIED_CATEGORY:
            raise MappingDataError(
                f"Runtime state {UNCLASSIFIED_CATEGORY!r} must not be defined as a formal category in {path}"
            )
        if not isinstance(raw_merchants, list) or not raw_merchants:
            raise MappingDataError(
                f"Category {category!r} in {path} must have a non-empty merchant list"
            )

        categories.add(category)
        for raw_merchant in raw_merchants:
            merchant = _require_non_empty_string(raw_merchant, "merchant name", path)
            existing_category = merchant_to_category.get(merchant)
            if existing_category is not None:
                raise MappingDataError(
                    f"Duplicate merchant {merchant!r} in {path}: already assigned to "
                    f"{existing_category!r}, cannot assign to {category!r}"
                )
            merchant_to_category[merchant] = category

    return frozenset(categories), merchant_to_category


def _validate_merchant_sets(
    merchant_names: set[str],
    merchant_to_category: Mapping[str, str],
    merchants_path: Path,
    categories_path: Path,
) -> None:
    categorized_merchants = set(merchant_to_category)
    missing_categories = sorted(merchant_names - categorized_merchants)
    unknown_merchants = sorted(categorized_merchants - merchant_names)
    if not missing_categories and not unknown_merchants:
        return

    details: list[str] = []
    if missing_categories:
        details.append(f"missing categories for {missing_categories!r}")
    if unknown_merchants:
        details.append(f"unknown merchants in categories {unknown_merchants!r}")
    detail_text = "; ".join(details)
    raise MappingDataError(
        f"Merchant set mismatch between {merchants_path} and {categories_path}: {detail_text}"
    )


def _load_overrides(path: Path, categories: frozenset[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    allowed_fields = {"transaction_id", "category", "note"}

    for line_number, line in enumerate(_read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw_record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MappingDataError(
                f"Unable to parse JSONL Mapping file {path} at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(raw_record, dict):
            raise MappingDataError(
                f"Invalid override in {path} at line {line_number}: expected a JSON object"
            )

        unknown_fields = sorted(set(raw_record) - allowed_fields)
        if unknown_fields:
            raise MappingDataError(
                f"Invalid override in {path} at line {line_number}: unknown fields {unknown_fields!r}"
            )

        transaction_id = _require_non_empty_string(
            raw_record.get("transaction_id"),
            f"transaction_id at line {line_number}",
            path,
        )
        category = _require_non_empty_string(
            raw_record.get("category"),
            f"override category at line {line_number}",
            path,
        )
        note = raw_record.get("note")
        if note is not None and not isinstance(note, str):
            raise MappingDataError(
                f"Invalid note in {path} at line {line_number}: expected a string, got {note!r}"
            )
        if transaction_id in overrides:
            raise MappingDataError(
                f"Duplicate override transaction_id {transaction_id!r} in {path} at line {line_number}"
            )
        if category not in categories:
            raise MappingDataError(
                f"Override transaction_id {transaction_id!r} in {path} at line {line_number} "
                f"references unknown category {category!r}"
            )
        overrides[transaction_id] = category

    return overrides


def load_merchant_mappings(
    merchants_path: Path = MERCHANTS_FILE,
    categories_path: Path = CATEGORIES_FILE,
    overrides_path: Path = TRANSACTION_CATEGORY_OVERRIDES_FILE,
) -> MerchantMappings:
    merchant_names, description_to_merchant = _load_merchants(merchants_path)
    categories, merchant_to_category = _load_categories(categories_path)
    _validate_merchant_sets(
        merchant_names,
        merchant_to_category,
        merchants_path,
        categories_path,
    )
    transaction_category_overrides = _load_overrides(overrides_path, categories)

    return MerchantMappings(
        description_to_merchant=MappingProxyType(description_to_merchant),
        merchant_to_category=MappingProxyType(merchant_to_category),
        transaction_category_overrides=MappingProxyType(transaction_category_overrides),
        categories=categories,
        merchants_path=merchants_path,
        categories_path=categories_path,
        overrides_path=overrides_path,
    )


def resolve_transaction(
    transaction: CmbTransaction,
    mappings: MerchantMappings,
) -> ResolvedTransaction:
    merchant_name = mappings.description_to_merchant.get(transaction.description)
    override_category = mappings.transaction_category_overrides.get(transaction.transaction_id)

    if merchant_name is None:
        if override_category is not None:
            raise MappingResolutionError(
                f"Override in {mappings.overrides_path} cannot be applied because description is not mapped "
                f"in {mappings.merchants_path}: transaction_id={transaction.transaction_id!r}, "
                f"description={transaction.description!r}, override_category={override_category!r}"
            )
        return ResolvedTransaction(
            transaction=transaction,
            merchant_name=None,
            display_name=transaction.description,
            default_category=None,
            category=UNCLASSIFIED_CATEGORY,
            category_source="unclassified",
            is_unmatched=True,
            review_signals=(),
        )

    default_category = mappings.merchant_to_category.get(merchant_name)
    if default_category is None:
        raise MappingResolutionError(
            f"Merchant {merchant_name!r} matched description {transaction.description!r} but has no category "
            f"in {mappings.categories_path}"
        )

    if override_category is not None:
        return ResolvedTransaction(
            transaction=transaction,
            merchant_name=merchant_name,
            display_name=merchant_name,
            default_category=default_category,
            category=override_category,
            category_source="transaction_override",
            is_unmatched=False,
            review_signals=(),
        )

    review_signals: tuple[str, ...] = ()
    if default_category == OTHER_EXPENSE_CATEGORY:
        review_signals = (OTHER_EXPENSE_REVIEW,)
    elif (
        default_category == GENERAL_SHOPPING_CATEGORY
        and transaction.amount <= HIGH_VALUE_GENERAL_SHOPPING_THRESHOLD
    ):
        review_signals = (HIGH_VALUE_GENERAL_SHOPPING_REVIEW,)

    return ResolvedTransaction(
        transaction=transaction,
        merchant_name=merchant_name,
        display_name=merchant_name,
        default_category=default_category,
        category=default_category,
        category_source="merchant_default",
        is_unmatched=False,
        review_signals=review_signals,
    )
