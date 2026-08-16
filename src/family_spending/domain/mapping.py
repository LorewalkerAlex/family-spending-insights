from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from family_spending.domain.errors import DomainInvariantError

UNCLASSIFIED_CATEGORY = "\u5f85\u5206\u7c7b"
OTHER_EXPENSE_CATEGORY = "\u5176\u4ed6\u652f\u51fa"


def _validate_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainInvariantError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise DomainInvariantError(f"{label} must not contain surrounding whitespace")


@dataclass(frozen=True)
class MappingCatalog:
    """Reviewed description-to-Merchant and Merchant-to-default-Category knowledge."""

    description_to_merchant: Mapping[str, str]
    merchant_to_category: Mapping[str, str]
    categories: frozenset[str]

    def __post_init__(self) -> None:
        descriptions = dict(self.description_to_merchant)
        merchant_categories = dict(self.merchant_to_category)
        categories = frozenset(self.categories)

        for description, merchant in descriptions.items():
            _validate_name(description, "description")
            _validate_name(merchant, "merchant")
        for merchant, category in merchant_categories.items():
            _validate_name(merchant, "merchant")
            _validate_name(category, "category")
        for category in categories:
            _validate_name(category, "category")

        if UNCLASSIFIED_CATEGORY in categories:
            raise DomainInvariantError(
                f"{UNCLASSIFIED_CATEGORY!r} is runtime state, not a formal category"
            )

        mapped_merchants = set(descriptions.values())
        categorized_merchants = set(merchant_categories)
        if mapped_merchants != categorized_merchants:
            missing = sorted(mapped_merchants - categorized_merchants)
            unknown = sorted(categorized_merchants - mapped_merchants)
            raise DomainInvariantError(
                "Mapping merchant sets must match exactly; "
                f"missing_categories={missing!r}, unknown_merchants={unknown!r}"
            )

        used_categories = set(merchant_categories.values())
        if categories != used_categories:
            missing = sorted(used_categories - categories)
            unused = sorted(categories - used_categories)
            raise DomainInvariantError(
                "Mapping categories must match reviewed merchant defaults exactly; "
                f"missing={missing!r}, unused={unused!r}"
            )

        object.__setattr__(self, "description_to_merchant", MappingProxyType(descriptions))
        object.__setattr__(self, "merchant_to_category", MappingProxyType(merchant_categories))
        object.__setattr__(self, "categories", categories)

    @classmethod
    def empty(cls) -> MappingCatalog:
        """Represent a household with no reviewed expense Mapping yet."""
        return cls({}, {}, frozenset())

    def merchant_for_description(self, description: str | None) -> str | None:
        if description is None:
            return None
        return self.description_to_merchant.get(description)

    def default_category_for_merchant(self, merchant: str | None) -> str | None:
        if merchant is None:
            return None
        return self.merchant_to_category.get(merchant)
