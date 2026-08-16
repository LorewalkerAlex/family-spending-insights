from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.mapping import MappingCatalog, UNCLASSIFIED_CATEGORY
from family_spending.persistence.filesystem.layout import StorageLayout


class MappingStoreError(RuntimeError):
    """Raised when reviewed Mapping state cannot satisfy the canonical storage contract."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys instead of silently overwriting them."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    """Build one YAML mapping while treating duplicate reviewed keys as corruption."""
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


def _read_yaml_mapping(path: Path) -> dict[object, object]:
    """Read one exact UTF-8 YAML mapping and surface format errors with its storage path."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MappingStoreError(f"Unable to read Mapping state {path}: {exc}") from exc
    try:
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise MappingStoreError(f"Unable to parse Mapping state {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MappingStoreError(
            f"Expected YAML mapping in {path}, got {type(value).__name__}"
        )
    return value


def _exact_text(value: object, *, label: str, path: Path) -> str:
    """Require reviewed names to already be normalized because they are exact lookup keys."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MappingStoreError(
            f"Invalid {label} in {path}: expected normalized non-empty text, got {value!r}"
        )
    return value


def _decode_catalog(merchants_path: Path, categories_path: Path) -> MappingCatalog:
    """Decode the two reviewed YAML indexes into the format-neutral Domain catalog."""
    raw_merchants = _read_yaml_mapping(merchants_path)
    raw_categories = _read_yaml_mapping(categories_path)

    description_to_merchant: dict[str, str] = {}
    merchant_names: set[str] = set()
    for raw_merchant, raw_descriptions in raw_merchants.items():
        merchant = _exact_text(raw_merchant, label="merchant", path=merchants_path)
        if not isinstance(raw_descriptions, list) or not raw_descriptions:
            raise MappingStoreError(
                f"Merchant {merchant!r} in {merchants_path} must have a non-empty description list"
            )
        merchant_names.add(merchant)
        for raw_description in raw_descriptions:
            description = _exact_text(
                raw_description,
                label="description",
                path=merchants_path,
            )
            previous = description_to_merchant.get(description)
            if previous is not None:
                raise MappingStoreError(
                    f"Description {description!r} is assigned to both {previous!r} and {merchant!r}"
                )
            description_to_merchant[description] = merchant

    merchant_to_category: dict[str, str] = {}
    categories: set[str] = set()
    for raw_category, raw_merchants_for_category in raw_categories.items():
        category = _exact_text(raw_category, label="category", path=categories_path)
        if category == UNCLASSIFIED_CATEGORY:
            raise MappingStoreError(
                f"Runtime category {UNCLASSIFIED_CATEGORY!r} must not be persisted as reviewed Mapping"
            )
        if not isinstance(raw_merchants_for_category, list) or not raw_merchants_for_category:
            raise MappingStoreError(
                f"Category {category!r} in {categories_path} must have a non-empty merchant list"
            )
        categories.add(category)
        for raw_merchant in raw_merchants_for_category:
            merchant = _exact_text(
                raw_merchant,
                label="merchant",
                path=categories_path,
            )
            previous = merchant_to_category.get(merchant)
            if previous is not None:
                raise MappingStoreError(
                    f"Merchant {merchant!r} is assigned to both {previous!r} and {category!r}"
                )
            merchant_to_category[merchant] = category

    if merchant_names != set(merchant_to_category):
        missing = sorted(merchant_names - set(merchant_to_category))
        unknown = sorted(set(merchant_to_category) - merchant_names)
        raise MappingStoreError(
            "Mapping merchant sets differ; "
            f"missing_categories={missing!r}, unknown_merchants={unknown!r}"
        )

    try:
        return MappingCatalog(
            description_to_merchant=description_to_merchant,
            merchant_to_category=merchant_to_category,
            categories=frozenset(categories),
        )
    except DomainInvariantError as exc:
        raise MappingStoreError(f"Invalid Mapping state: {exc}") from exc


def _yaml_text(payload: dict[str, list[str]]) -> str:
    """Serialize reviewed Mapping deterministically without YAML aliases or implicit sorting."""
    return yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )


def _atomic_write_text(path: Path, text: str) -> None:
    """Publish one UTF-8 Mapping file atomically; multi-file rollback is owned by FileUnitOfWork."""
    path.parent.mkdir(parents=True, exist_ok=True)
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
        raise MappingStoreError(f"Unable to persist Mapping state {path}: {exc}") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class FilesystemMappingStore:
    """Persist active reviewed Mapping knowledge in the canonical household data root."""

    layout: StorageLayout

    def load(self) -> MappingCatalog:
        merchants_exists = self.layout.merchant_mappings.exists()
        categories_exists = self.layout.category_mappings.exists()
        if not merchants_exists and not categories_exists:
            return MappingCatalog.empty()
        if merchants_exists != categories_exists:
            raise MappingStoreError(
                "Canonical Mapping state is incomplete: merchants.yaml and categories.yaml must exist together"
            )
        return _decode_catalog(
            self.layout.merchant_mappings,
            self.layout.category_mappings,
        )

    def replace(self, mappings: MappingCatalog) -> None:
        """Replace reviewed Mapping state; callers coordinate both files in a UnitOfWork."""
        if not mappings.description_to_merchant:
            self.layout.merchant_mappings.unlink(missing_ok=True)
            self.layout.category_mappings.unlink(missing_ok=True)
            return

        descriptions_by_merchant: defaultdict[str, list[str]] = defaultdict(list)
        for description, merchant in mappings.description_to_merchant.items():
            descriptions_by_merchant[merchant].append(description)

        merchants_payload = {
            merchant: sorted(descriptions_by_merchant[merchant])
            for merchant in sorted(descriptions_by_merchant)
        }
        merchants_by_category: defaultdict[str, list[str]] = defaultdict(list)
        for merchant, category in mappings.merchant_to_category.items():
            merchants_by_category[category].append(merchant)
        categories_payload = {
            category: sorted(merchants_by_category[category])
            for category in sorted(merchants_by_category)
        }

        _atomic_write_text(
            self.layout.merchant_mappings,
            _yaml_text(merchants_payload),
        )
        _atomic_write_text(
            self.layout.category_mappings,
            _yaml_text(categories_payload),
        )
