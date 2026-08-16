from __future__ import annotations

from collections.abc import Callable
from typing import Final

from family_spending.application.errors import (
    ApplicationNotFoundError,
    ApplicationValidationError,
)
from family_spending.application.models import TransactionView
from family_spending.application.ports.runtime import MutationExecutor, RuntimeReader
from family_spending.application.ports.storage import (
    EnrichmentDecisionStore,
    UnitOfWorkProvider,
)
from family_spending.domain.enrichment import EnrichmentDecision
from family_spending.domain.errors import DomainInvariantError

UNSET: Final = object()


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApplicationValidationError(f"{field} must be a string or null")
    stripped = value.strip()
    return stripped or None


def update_decision_collection(
    decisions: tuple[EnrichmentDecision, ...],
    transaction_id: str,
    *,
    merchant: object = UNSET,
    category: object = UNSET,
    note: object = UNSET,
) -> tuple[EnrichmentDecision, ...]:
    """Apply sparse field changes while deleting an all-null decision instead of materializing defaults."""
    current = next(
        (decision for decision in decisions if decision.transaction_id == transaction_id),
        None,
    )
    if merchant is UNSET:
        merchant_value = current.merchant_override if current is not None else None
    else:
        merchant_value = _optional_text(merchant, "merchant")
    if category is UNSET:
        category_value = current.category_override if current is not None else None
    else:
        category_value = _optional_text(category, "category")
    if note is UNSET:
        note_value = current.note if current is not None else None
    else:
        note_value = _optional_text(note, "note")

    remaining = tuple(
        decision for decision in decisions if decision.transaction_id != transaction_id
    )
    if merchant_value is None and category_value is None and note_value is None:
        return remaining
    try:
        updated = EnrichmentDecision(
            transaction_id=transaction_id,
            merchant_override=merchant_value,
            category_override=category_value,
            note=note_value,
        )
    except DomainInvariantError as exc:
        raise ApplicationValidationError(str(exc)) from exc
    return remaining + (updated,)


class EnrichmentCommandService:
    """Persist sparse Transaction-level decisions and let Runtime rebuild derived Enrichment."""

    def __init__(
        self,
        *,
        decision_store: EnrichmentDecisionStore,
        runtime: RuntimeReader,
        coordinator: MutationExecutor,
        unit_of_work_provider: UnitOfWorkProvider,
        transaction_view: Callable[[str], TransactionView],
    ) -> None:
        self._store = decision_store
        self._runtime = runtime
        self._coordinator = coordinator
        self._uow = unit_of_work_provider
        self._transaction_view = transaction_view

    def update(
        self,
        transaction_id: str,
        *,
        merchant: object = UNSET,
        category: object = UNSET,
        note: object = UNSET,
    ) -> TransactionView:
        if merchant is UNSET and category is UNSET and note is UNSET:
            raise ApplicationValidationError(
                "Enrichment update requires at least one of merchant, category, or note"
            )

        def mutation() -> None:
            state = self._runtime.current_state()
            transaction = state.indexes.transaction_by_id.get(transaction_id)
            if transaction is None:
                raise ApplicationNotFoundError(
                    f"Transaction {transaction_id!r} does not exist"
                )
            if transaction.transaction_type == "income" and (
                merchant is not UNSET or category is not UNSET
            ):
                raise ApplicationValidationError(
                    "Income Enrichment supports Note decisions only"
                )
            if category is not UNSET:
                category_value = _optional_text(category, "category")
                if (
                    category_value is not None
                    and category_value not in state.household.mappings.categories
                ):
                    raise ApplicationValidationError(
                        f"Unknown category {category_value!r}; use a formal configured category"
                    )
            decisions = update_decision_collection(
                self._store.load(),
                transaction_id,
                merchant=merchant,
                category=category,
                note=note,
            )
            self._store.replace(decisions)

        self._coordinator.execute(
            label="Enrichment update",
            unit_of_work=self._uow.open("enrichment", label="Enrichment update"),
            mutation=mutation,
        )
        return self._transaction_view(transaction_id)
