from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal

from family_spending.application.errors import (
    ApplicationConflictError,
    ApplicationValidationError,
)
from family_spending.application.models import (
    MappingReviewItem,
    MappingReviewPreview,
    MappingReviewWorkspaceView,
    MerchantMappingOption,
)
from family_spending.application.ports.runtime import MutationExecutor, RuntimeReader
from family_spending.application.ports.storage import MappingStore, UnitOfWorkProvider
from family_spending.domain.enrichment import resolve_enrichments
from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.mapping import MappingCatalog


class MappingReviewService:
    """Preview and apply deterministic reviewed Mapping changes without rerunning Reconciliation."""

    def __init__(
        self,
        *,
        mapping_store: MappingStore,
        runtime: RuntimeReader,
        coordinator: MutationExecutor,
        unit_of_work_provider: UnitOfWorkProvider,
    ) -> None:
        self._mapping_store = mapping_store
        self._runtime = runtime
        self._coordinator = coordinator
        self._uow = unit_of_work_provider

    def workspace(self) -> MappingReviewWorkspaceView:
        state = self._runtime.current_state()
        grouped: dict[str, list[str]] = defaultdict(list)
        for transaction in state.household.transactions:
            if transaction.transaction_type != "expense":
                continue
            source = state.indexes.authoritative_source_by_transaction_id[transaction.id]
            description = source.description
            if (
                description is None
                or description in state.household.mappings.description_to_merchant
            ):
                continue
            grouped[description].append(transaction.id)

        decisions_by_id = {
            decision.transaction_id: decision
            for decision in state.household.enrichment_decisions
        }
        items: list[MappingReviewItem] = []
        for description, transaction_ids in grouped.items():
            transactions = [state.indexes.transaction_by_id[item] for item in transaction_ids]
            currencies = {transaction.currency for transaction in transactions}
            if len(currencies) != 1:
                raise ApplicationConflictError(
                    f"Unmapped description {description!r} spans multiple currencies"
                )
            source_types = tuple(
                sorted(
                    {
                        state.indexes.authoritative_source_by_transaction_id[item].source_type
                        for item in transaction_ids
                    }
                )
            )
            items.append(
                MappingReviewItem(
                    description=description,
                    transaction_count=len(transactions),
                    total_amount=sum(
                        (transaction.amount for transaction in transactions),
                        start=Decimal("0"),
                    ),
                    currency=next(iter(currencies)),
                    latest_date=max(transaction.transaction_date for transaction in transactions),
                    source_types=source_types,
                    transaction_only_exception_count=sum(
                        decisions_by_id.get(item) is not None
                        and decisions_by_id[item].merchant_override is not None
                        for item in transaction_ids
                    ),
                )
            )
        mappings = state.household.mappings
        return MappingReviewWorkspaceView(
            items=tuple(
                sorted(items, key=lambda item: (item.latest_date, item.description), reverse=True)
            ),
            merchants=tuple(
                MerchantMappingOption(name, mappings.merchant_to_category[name])
                for name in sorted(mappings.merchant_to_category)
            ),
            categories=tuple(sorted(mappings.categories)),
        )

    def preview(self, *, description: str, merchant: str, category: str) -> MappingReviewPreview:
        state = self._runtime.current_state()
        return self._plan(state, description, merchant, category)[0]

    def apply(
        self,
        *,
        description: str,
        merchant: str,
        category: str,
        preview_token: str,
        confirm_new_merchant: bool = False,
    ) -> MappingReviewPreview:
        def mutation() -> MappingReviewPreview:
            state = self._runtime.current_state()
            preview, next_catalog = self._plan(state, description, merchant, category)
            if preview.token != preview_token:
                raise ApplicationConflictError(
                    "Mapping Review state changed after preview; refresh before applying"
                )
            if preview.is_new_merchant and not confirm_new_merchant:
                raise ApplicationValidationError(
                    "Creating a new Merchant requires explicit confirmation"
                )
            self._mapping_store.replace(next_catalog)
            return preview

        return self._coordinator.execute(
            label="Mapping Review apply",
            unit_of_work=self._uow.open("mapping_review", label="Mapping Review apply"),
            mutation=mutation,
        )

    def _plan(
        self,
        state,
        description: str,
        merchant: str,
        category: str,
    ) -> tuple[MappingReviewPreview, MappingCatalog]:
        description = self._text(description, "description")
        merchant = self._text(merchant, "merchant")
        category = self._text(category, "category")
        mappings = state.household.mappings
        if description in mappings.description_to_merchant:
            raise ApplicationConflictError(
                f"Description {description!r} is already mapped; refresh Mapping Review"
            )
        if category not in mappings.categories:
            raise ApplicationValidationError(f"Unknown category {category!r}")

        matching = {
            transaction.id
            for transaction in state.household.transactions
            if transaction.transaction_type == "expense"
            and state.indexes.authoritative_source_by_transaction_id[transaction.id].description
            == description
        }
        if not matching:
            raise ApplicationValidationError(
                f"Expense description {description!r} does not exist in the current snapshot"
            )

        previous_category = mappings.merchant_to_category.get(merchant)
        is_new = previous_category is None
        if not is_new and previous_category != category:
            remaining_in_previous = sum(
                mapped_category == previous_category
                for mapped_merchant, mapped_category in mappings.merchant_to_category.items()
                if mapped_merchant != merchant
            )
            if remaining_in_previous == 0:
                raise ApplicationValidationError(
                    f"Cannot move the last Merchant out of Category {previous_category!r}"
                )

        description_to_merchant = dict(mappings.description_to_merchant)
        merchant_to_category = dict(mappings.merchant_to_category)
        description_to_merchant[description] = merchant
        merchant_to_category[merchant] = category
        try:
            next_catalog = MappingCatalog(
                description_to_merchant,
                merchant_to_category,
                frozenset(merchant_to_category.values()),
            )
        except DomainInvariantError as exc:
            raise ApplicationValidationError(str(exc)) from exc

        before_by_id = state.indexes.enrichment_by_transaction_id
        after = resolve_enrichments(
            state.household.transactions,
            state.indexes.authoritative_source_by_transaction_id,
            next_catalog,
            state.household.enrichment_decisions,
        )
        after_by_id = {item.transaction_id: item for item in after}
        changed = {
            transaction_id
            for transaction_id, before in before_by_id.items()
            if after_by_id[transaction_id] != before
        }
        description_changed = changed & matching
        category_changed = changed - matching
        decisions_by_id = {
            decision.transaction_id: decision
            for decision in state.household.enrichment_decisions
        }
        preserved_merchant = sum(
            decisions_by_id.get(transaction_id) is not None
            and decisions_by_id[transaction_id].merchant_override is not None
            for transaction_id in matching
        )
        preserved_category = sum(
            decisions_by_id.get(transaction_id) is not None
            and decisions_by_id[transaction_id].category_override is not None
            and before_by_id[transaction_id].category == after_by_id[transaction_id].category
            for transaction_id in changed
        )
        token = self._token(
            state,
            description=description,
            merchant=merchant,
            category=category,
            matching=matching,
        )
        return (
            MappingReviewPreview(
                token=token,
                description=description,
                merchant=merchant,
                category=category,
                is_new_merchant=is_new,
                previous_default_category=previous_category,
                description_transaction_count=len(matching),
                description_affected_transaction_count=len(description_changed),
                default_category_affected_transaction_count=len(category_changed),
                total_affected_transaction_count=len(changed),
                preserved_merchant_exception_count=preserved_merchant,
                preserved_category_exception_count=preserved_category,
            ),
            next_catalog,
        )

    @staticmethod
    def _token(state, *, description: str, merchant: str, category: str, matching: set[str]) -> str:
        """Bind Apply to all financial inputs that could change this Mapping impact plan."""
        mappings = state.household.mappings
        payload = {
            "description": description,
            "merchant": merchant,
            "category": category,
            "description_to_merchant": sorted(mappings.description_to_merchant.items()),
            "merchant_to_category": sorted(mappings.merchant_to_category.items()),
            "transactions": [
                {
                    "id": transaction.id,
                    "type": transaction.transaction_type,
                    "description": state.indexes.authoritative_source_by_transaction_id[
                        transaction.id
                    ].description,
                }
                for transaction in state.household.transactions
            ],
            "matching": sorted(matching),
            "decisions": [
                {
                    "transaction_id": decision.transaction_id,
                    "merchant_override": decision.merchant_override,
                    "category_override": decision.category_override,
                    "note": decision.note,
                }
                for decision in state.household.enrichment_decisions
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ApplicationValidationError(f"{field} must be non-empty text")
        return value.strip()
