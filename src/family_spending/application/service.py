from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from family_spending.application.enrichment import UNSET, EnrichmentCommandService
from family_spending.application.errors import ApplicationValidationError
from family_spending.application.feedback import FeedbackService
from family_spending.application.manual_input import ManualInputService
from family_spending.application.mapping_review import MappingReviewService
from family_spending.application.queries import QueryService
from family_spending.application.scheduling import ScheduledInputService
from family_spending.application.source_sync import SourceSyncResult, SourceSyncService
from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.feedback import FEEDBACK_RUNTIMES, FeedbackContext, FeedbackItem
from family_spending.domain.scheduling import ScheduledRule
from family_spending.sources.manual.model import create_manual_evidence


class FamilySpendingApplication:
    """Canonical use-case facade shared by future HTTP and CLI interfaces."""

    def __init__(
        self,
        *,
        source_sync: SourceSyncService,
        queries: QueryService,
        manual_input: ManualInputService,
        enrichment: EnrichmentCommandService,
        mapping_review: MappingReviewService,
        scheduling: ScheduledInputService,
        feedback: FeedbackService,
    ) -> None:
        self.source_sync_service = source_sync
        self.queries = queries
        self.manual_input = manual_input
        self.enrichment = enrichment
        self.mapping_review = mapping_review
        self.scheduling = scheduling
        self.feedback = feedback

    def initialize(self, *, as_of: date | None = None) -> None:
        """Bring pending Sources current, then materialize due schedules through the same Application paths."""
        self.source_sync_service.sync()
        self.scheduling.run_due(as_of or date.today())

    def sync_sources(self) -> SourceSyncResult:
        return self.source_sync_service.sync()

    def list_transactions(self):
        return self.queries.list_transactions()

    def get_transaction(self, transaction_id: str):
        return self.queries.get_transaction(transaction_id)

    def list_categories(self) -> tuple[str, ...]:
        return self.queries.list_categories()

    def get_spending_statistics(self) -> dict[str, object]:
        return self.queries.get_spending_statistics()

    def get_financial_summary(self) -> dict[str, object]:
        return self.queries.get_financial_summary()

    def list_manual_descriptions(self) -> tuple[str, ...]:
        return self.queries.list_manual_descriptions()

    def list_manual_inputs(self):
        return self.queries.list_manual_inputs()

    def create_manual_input(
        self,
        *,
        transaction_type: object,
        transaction_date: object,
        amount: object,
        description: object,
        note: object = None,
    ):
        evidence = self._manual_evidence(
            transaction_type=transaction_type,
            transaction_date=transaction_date,
            amount=amount,
            description=description,
        )
        return self.manual_input.create(
            evidence,
            note=self._optional_text(note, "note"),
        )

    def correct_manual_input(
        self,
        evidence_id: str,
        *,
        transaction_type: object,
        transaction_date: object,
        amount: object,
        description: object,
        note: object = UNSET,
    ):
        evidence = self._manual_evidence(
            transaction_type=transaction_type,
            transaction_date=transaction_date,
            amount=amount,
            description=description,
            evidence_id=self._required_text(evidence_id, "evidence_id"),
        )
        note_value = UNSET if note is UNSET else self._optional_text(note, "note")
        return self.manual_input.correct(evidence.evidence_id, evidence, note=note_value)

    def delete_manual_input(self, evidence_id: str):
        return self.manual_input.delete(self._required_text(evidence_id, "evidence_id"))

    def update_enrichment(
        self,
        transaction_id: str,
        *,
        merchant: object = UNSET,
        category: object = UNSET,
        note: object = UNSET,
    ):
        return self.enrichment.update(
            self._required_text(transaction_id, "transaction_id"),
            merchant=merchant,
            category=category,
            note=note,
        )

    def get_mapping_review_workspace(self):
        return self.mapping_review.workspace()

    def preview_mapping_review(
        self,
        *,
        description: object,
        merchant: object,
        category: object,
    ):
        return self.mapping_review.preview(
            description=self._required_text(description, "description"),
            merchant=self._required_text(merchant, "merchant"),
            category=self._required_text(category, "category"),
        )

    def apply_mapping_review(
        self,
        *,
        description: object,
        merchant: object,
        category: object,
        preview_token: object,
        confirm_new_merchant: object = False,
    ):
        if not isinstance(confirm_new_merchant, bool):
            raise ApplicationValidationError("confirm_new_merchant must be a boolean")
        return self.mapping_review.apply(
            description=self._required_text(description, "description"),
            merchant=self._required_text(merchant, "merchant"),
            category=self._required_text(category, "category"),
            preview_token=self._required_text(preview_token, "preview_token"),
            confirm_new_merchant=confirm_new_merchant,
        )

    def list_scheduled_inputs(self) -> tuple[ScheduledRule, ...]:
        return self.scheduling.list_rules()

    def list_scheduled_input_views(self):
        """Expose rule plus execution state for transport adapters without leaking stores."""
        return self.scheduling.list_rule_views()

    def get_scheduled_input_view(self, rule_id: str):
        return self.scheduling.get_rule_view(self._required_text(rule_id, "rule_id"))

    def create_scheduled_input(
        self,
        *,
        transaction_type: object,
        amount: object,
        description: object,
        next_date: object,
        note: object = None,
        enabled: object = True,
        as_of: date | None = None,
    ) -> ScheduledRule:
        return self.scheduling.create_rule(
            transaction_type=self._transaction_type(transaction_type),
            amount=self._amount_for_type(transaction_type, amount),
            description=self._required_text(description, "description"),
            first_occurrence_date=self._scheduled_date(next_date),
            note=self._optional_text(note, "note"),
            enabled=self._boolean(enabled, "enabled"),
            as_of=as_of,
        )

    def update_scheduled_input(
        self,
        rule_id: str,
        *,
        transaction_type: object,
        amount: object,
        description: object,
        next_date: object,
        note: object = None,
        enabled: object = True,
        as_of: date | None = None,
    ) -> ScheduledRule:
        return self.scheduling.update_rule(
            self._required_text(rule_id, "rule_id"),
            transaction_type=self._transaction_type(transaction_type),
            amount=self._amount_for_type(transaction_type, amount),
            description=self._required_text(description, "description"),
            first_occurrence_date=self._scheduled_date(next_date),
            note=self._optional_text(note, "note"),
            enabled=self._boolean(enabled, "enabled"),
            as_of=as_of,
        )

    def delete_scheduled_input(self, rule_id: str) -> ScheduledRule:
        return self.scheduling.delete_rule(self._required_text(rule_id, "rule_id"))

    def run_due_scheduled_inputs(self, as_of: date | None = None):
        return self.scheduling.run_due(as_of or date.today())

    def list_feedback(self) -> tuple[FeedbackItem, ...]:
        return self.feedback.list_items()

    def create_feedback(self, *, content: object, context: object = None) -> FeedbackItem:
        return self.feedback.create(
            content=self._required_text(content, "content"),
            context=self._feedback_context(context),
        )

    def update_feedback(self, feedback_id: str, *, status: object) -> FeedbackItem:
        return self.feedback.update_status(
            self._required_text(feedback_id, "feedback_id"),
            self._required_text(status, "status"),
        )

    def _manual_evidence(
        self,
        *,
        transaction_type: object,
        transaction_date: object,
        amount: object,
        description: object,
        evidence_id: str | None = None,
    ):
        try:
            parsed_type = self._transaction_type(transaction_type)
            return create_manual_evidence(
                transaction_type=parsed_type,
                transaction_date=self._date(transaction_date, "date"),
                amount=self._amount_for_type(parsed_type, amount),
                description=self._required_text(description, "description"),
                evidence_id=evidence_id,
            )
        except DomainInvariantError as exc:
            raise ApplicationValidationError(str(exc)) from exc

    @staticmethod
    def _transaction_type(value: object) -> str:
        parsed = FamilySpendingApplication._required_text(value, "type")
        if parsed not in {"income", "expense"}:
            raise ApplicationValidationError("type must be either 'income' or 'expense'")
        return parsed

    @staticmethod
    def _date(value: object, field: str) -> date:
        text = FamilySpendingApplication._required_text(value, field)
        try:
            return date.fromisoformat(text)
        except ValueError as exc:
            raise ApplicationValidationError(
                f"{field} must use YYYY-MM-DD format, got {text!r}"
            ) from exc

    @staticmethod
    def _scheduled_date(value: object) -> date:
        parsed = FamilySpendingApplication._date(value, "next_date")
        if parsed.day > 28:
            raise ApplicationValidationError(
                "Scheduled Input V1 only supports monthly occurrence days 1-28"
            )
        return parsed

    @staticmethod
    def _decimal(value: object, field: str) -> Decimal:
        text = FamilySpendingApplication._required_text(value, field)
        try:
            parsed = Decimal(text)
        except (InvalidOperation, ValueError) as exc:
            raise ApplicationValidationError(
                f"{field} must be a decimal string, got {text!r}"
            ) from exc
        if not parsed.is_finite():
            raise ApplicationValidationError(
                f"{field} must be a finite decimal string, got {text!r}"
            )
        return parsed

    @staticmethod
    def _amount_for_type(transaction_type: object, amount: object) -> Decimal:
        parsed_type = FamilySpendingApplication._transaction_type(transaction_type)
        parsed_amount = FamilySpendingApplication._decimal(amount, "amount")
        if parsed_type == "income" and parsed_amount <= 0:
            raise ApplicationValidationError("Income amount must be positive")
        return parsed_amount

    @staticmethod
    def _boolean(value: object, field: str) -> bool:
        if not isinstance(value, bool):
            raise ApplicationValidationError(f"{field} must be a boolean")
        return value

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ApplicationValidationError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_text(value: object, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ApplicationValidationError(f"{field} must be a string or null")
        stripped = value.strip()
        return stripped or None

    @staticmethod
    def _feedback_context(value: object) -> FeedbackContext:
        if value is None:
            return FeedbackContext()
        if not isinstance(value, dict):
            raise ApplicationValidationError("Feedback context must be an object or null")
        allowed = {"runtime", "page", "workspace", "entity_type", "entity_id"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ApplicationValidationError(
                f"Unknown Feedback context fields: {unknown!r}"
            )
        runtime = FamilySpendingApplication._optional_text(
            value.get("runtime"), "context.runtime"
        )
        if runtime is not None and runtime not in FEEDBACK_RUNTIMES:
            raise ApplicationValidationError(
                f"Feedback runtime must be one of {sorted(FEEDBACK_RUNTIMES)!r}"
            )
        try:
            return FeedbackContext(
                runtime=runtime,
                page=FamilySpendingApplication._optional_text(
                    value.get("page"), "context.page"
                ),
                workspace=FamilySpendingApplication._optional_text(
                    value.get("workspace"), "context.workspace"
                ),
                entity_type=FamilySpendingApplication._optional_text(
                    value.get("entity_type"), "context.entity_type"
                ),
                entity_id=FamilySpendingApplication._optional_text(
                    value.get("entity_id"), "context.entity_id"
                ),
            )
        except DomainInvariantError as exc:
            raise ApplicationValidationError(str(exc)) from exc
