from __future__ import annotations

import uuid
from datetime import datetime, timezone

from family_spending.application.errors import (
    ApplicationNotFoundError,
    ApplicationValidationError,
)
from family_spending.application.ports.runtime import MutationExecutor
from family_spending.application.ports.storage import (
    FeedbackStore,
    UnitOfWorkProvider,
)
from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.feedback import (
    FEEDBACK_STATUSES,
    FeedbackContext,
    FeedbackItem,
    FeedbackStatus,
    update_feedback_status,
)


class FeedbackService:
    """Own durable product Feedback while sharing the process-wide single-writer boundary."""

    def __init__(
        self,
        *,
        store: FeedbackStore,
        coordinator: MutationExecutor,
        unit_of_work_provider: UnitOfWorkProvider,
    ) -> None:
        self._store = store
        self._coordinator = coordinator
        self._uow = unit_of_work_provider

    def list_items(self) -> tuple[FeedbackItem, ...]:
        return tuple(reversed(self._store.load()))

    def create(self, *, content: str, context: FeedbackContext | None = None) -> FeedbackItem:
        content = content.strip() if isinstance(content, str) else ""
        if not content:
            raise ApplicationValidationError("Feedback content must be non-empty text")
        try:
            item = FeedbackItem(
                id=f"feedback_{uuid.uuid4().hex}",
                created_at=datetime.now(timezone.utc),
                status="open",
                content=content,
                context=context or FeedbackContext(),
            )
        except DomainInvariantError as exc:
            raise ApplicationValidationError(str(exc)) from exc

        def mutation() -> None:
            items = self._store.load()
            self._store.replace(items + (item,))

        self._coordinator.execute(
            label="Feedback create",
            unit_of_work=self._uow.open("feedback", label="Feedback create"),
            mutation=mutation,
        )
        return item

    def update_status(self, feedback_id: str, status: FeedbackStatus) -> FeedbackItem:
        if status not in FEEDBACK_STATUSES:
            raise ApplicationValidationError(
                "Feedback status must be either 'open' or 'resolved'"
            )
        def mutation() -> FeedbackItem:
            items = self._store.load()
            current = next((item for item in items if item.id == feedback_id), None)
            if current is None:
                raise ApplicationNotFoundError(
                    f"Feedback {feedback_id!r} does not exist"
                )
            if current.status == status:
                return current
            try:
                updated = update_feedback_status(current, status)
            except DomainInvariantError as exc:
                raise ApplicationValidationError(str(exc)) from exc
            self._store.replace(
                tuple(updated if item.id == feedback_id else item for item in items)
            )
            return updated

        return self._coordinator.execute(
            label="Feedback status update",
            unit_of_work=self._uow.open("feedback", label="Feedback status update"),
            mutation=mutation,
        )
