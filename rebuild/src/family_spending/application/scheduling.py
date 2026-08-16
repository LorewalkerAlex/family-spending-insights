from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date
from typing import Protocol

from family_spending.application.enrichment import update_decision_collection
from family_spending.application.errors import (
    ApplicationNotFoundError,
    ApplicationStateError,
    ApplicationValidationError,
)
from family_spending.application.models import (
    ScheduledInputOccurrence,
    ScheduledInputRuleView,
    ScheduledInputRunResult,
)
from family_spending.application.ports.runtime import MutationExecutor, RuntimeReader
from family_spending.application.ports.storage import (
    EnrichmentDecisionStore,
    IdentityStore,
    ScheduleStore,
    UnitOfWorkProvider,
)
from family_spending.application.source_sync import SourceSyncResult, SourceSyncService
from family_spending.domain.scheduling import (
    ScheduleExecutionState,
    ScheduledRule,
    next_monthly_date,
    next_occurrence_date,
    scheduled_occurrence_identity,
)
from family_spending.sources.manual.model import (
    MANUAL_CURRENCY,
    ManualEvidence,
    create_manual_evidence,
    manual_evidence_to_source_record,
)


class ManualEvidenceRepository(Protocol):
    def load_all(self) -> tuple[ManualEvidence, ...]: ...

    def replace_all(self, records: tuple[ManualEvidence, ...]) -> None: ...


class ScheduledInputService:
    """Own monthly rule CRUD and idempotent Materialize-as-Manual-Evidence execution."""

    def __init__(
        self,
        *,
        schedule_store: ScheduleStore,
        manual_evidence_store: ManualEvidenceRepository,
        enrichment_store: EnrichmentDecisionStore,
        identity_store: IdentityStore,
        source_sync: SourceSyncService,
        runtime: RuntimeReader,
        coordinator: MutationExecutor,
        unit_of_work_provider: UnitOfWorkProvider,
    ) -> None:
        self._schedule = schedule_store
        self._manual = manual_evidence_store
        self._enrichment = enrichment_store
        self._identity = identity_store
        self._source_sync = source_sync
        self._runtime = runtime
        self._coordinator = coordinator
        self._uow = unit_of_work_provider

    def list_rules(self) -> tuple[ScheduledRule, ...]:
        return self._schedule.load_rules()

    def list_rule_views(self) -> tuple[ScheduledInputRuleView, ...]:
        """Join rules and durable execution metadata without leaking persistence shape."""
        rules = self._schedule.load_rules()
        execution_by_rule = {state.rule_id: state for state in self._schedule.load_execution()}
        unknown = sorted(set(execution_by_rule) - {rule.id for rule in rules})
        if unknown:
            raise ApplicationStateError(
                f"Schedule execution references missing rules: {unknown!r}"
            )
        return tuple(
            self._rule_view(rule, execution_by_rule.get(rule.id))
            for rule in rules
        )

    def get_rule_view(self, rule_id: str) -> ScheduledInputRuleView:
        view = next((item for item in self.list_rule_views() if item.id == rule_id), None)
        if view is None:
            raise ApplicationNotFoundError(f"Scheduled Rule {rule_id!r} does not exist")
        return view

    @staticmethod
    def _rule_view(
        rule: ScheduledRule,
        execution: ScheduleExecutionState | None,
    ) -> ScheduledInputRuleView:
        last_date = execution.last_processed_occurrence_date if execution is not None else None
        if execution is not None and last_date is not None and (
            execution.last_source_record_id is None
            or execution.last_transaction_id is None
            or execution.last_action is None
        ):
            raise ApplicationStateError(
                f"Schedule execution for {rule.id!r} is missing last-run metadata"
            )
        return ScheduledInputRuleView(
            id=rule.id,
            enabled=rule.enabled,
            transaction_type=rule.transaction_type,
            amount=rule.amount,
            currency=rule.currency,
            description=rule.description,
            note=rule.note,
            next_date=next_occurrence_date(rule, execution),
            last_occurrence_date=last_date,
            last_source_record_id=(
                execution.last_source_record_id if execution is not None else None
            ),
            last_transaction_id=(
                execution.last_transaction_id if execution is not None else None
            ),
            last_action=execution.last_action if execution is not None else None,
        )

    def create_rule(
        self,
        *,
        transaction_type: str,
        amount,
        description: str,
        first_occurrence_date: date,
        note: str | None = None,
        enabled: bool = True,
        currency: str = MANUAL_CURRENCY,
        as_of: date | None = None,
        rule_id: str | None = None,
    ) -> ScheduledRule:
        try:
            rule = ScheduledRule(
                id=rule_id or f"schedule_{uuid.uuid4().hex}",
                enabled=enabled,
                transaction_type=transaction_type,
                amount=amount,
                description=description.strip(),
                first_occurrence_date=first_occurrence_date,
                currency=currency.strip().upper(),
                note=note.strip() if isinstance(note, str) and note.strip() else None,
            )
        except Exception as exc:
            raise ApplicationValidationError(str(exc)) from exc

        def mutation() -> None:
            rules = self._schedule.load_rules()
            if any(item.id == rule.id for item in rules):
                raise ApplicationValidationError(f"Scheduled Rule {rule.id!r} already exists")
            self._schedule.replace_rules(rules + (rule,))
            self._run_due_inside_mutation(as_of or date.today())

        self._coordinator.execute(
            label="Scheduled Input rule create",
            unit_of_work=self._uow.open("scheduled_run", label="Scheduled Input rule create"),
            mutation=mutation,
        )
        return self._get_rule(rule.id)

    def update_rule(
        self,
        rule_id: str,
        *,
        transaction_type: str,
        amount,
        description: str,
        first_occurrence_date: date,
        note: str | None,
        enabled: bool,
        as_of: date | None = None,
    ) -> ScheduledRule:
        def mutation() -> None:
            rules = self._schedule.load_rules()
            current = next((rule for rule in rules if rule.id == rule_id), None)
            if current is None:
                raise ApplicationNotFoundError(f"Scheduled Rule {rule_id!r} does not exist")
            execution = next(
                (state for state in self._schedule.load_execution() if state.rule_id == rule_id),
                None,
            )
            if (
                execution is not None
                and execution.last_processed_occurrence_date is not None
                and first_occurrence_date <= execution.last_processed_occurrence_date
            ):
                raise ApplicationValidationError(
                    "Updated next occurrence must be after the last processed occurrence"
                )
            try:
                updated = replace(
                    current,
                    enabled=enabled,
                    transaction_type=transaction_type,
                    amount=amount,
                    description=description.strip(),
                    first_occurrence_date=first_occurrence_date,
                    note=note.strip() if isinstance(note, str) and note.strip() else None,
                )
            except Exception as exc:
                raise ApplicationValidationError(str(exc)) from exc
            self._schedule.replace_rules(
                tuple(updated if rule.id == rule_id else rule for rule in rules)
            )
            self._run_due_inside_mutation(as_of or date.today())

        self._coordinator.execute(
            label="Scheduled Input rule update",
            unit_of_work=self._uow.open("scheduled_run", label="Scheduled Input rule update"),
            mutation=mutation,
        )
        return self._get_rule(rule_id)

    def delete_rule(self, rule_id: str) -> ScheduledRule:
        def mutation() -> ScheduledRule:
            rules = self._schedule.load_rules()
            current = next((rule for rule in rules if rule.id == rule_id), None)
            if current is None:
                raise ApplicationNotFoundError(
                    f"Scheduled Rule {rule_id!r} does not exist"
                )
            self._schedule.replace_rules(
                tuple(rule for rule in rules if rule.id != rule_id)
            )
            self._schedule.replace_execution(
                tuple(
                    state
                    for state in self._schedule.load_execution()
                    if state.rule_id != rule_id
                )
            )
            return current

        return self._coordinator.execute(
            label="Scheduled Input rule delete",
            unit_of_work=self._uow.open(
                "schedule_rules", label="Scheduled Input rule delete"
            ),
            mutation=mutation,
        )

    def run_due(self, as_of: date) -> ScheduledInputRunResult:
        return self._coordinator.execute(
            label="Scheduled Input due run",
            unit_of_work=self._uow.open("scheduled_run", label="Scheduled Input due run"),
            mutation=lambda: self._run_due_inside_mutation(as_of),
        )

    def _run_due_inside_mutation(self, as_of: date) -> ScheduledInputRunResult:
        rules = self._schedule.load_rules()
        execution_by_rule = {
            state.rule_id: state for state in self._schedule.load_execution()
        }
        unknown_execution = sorted(set(execution_by_rule) - {rule.id for rule in rules})
        if unknown_execution:
            raise ApplicationStateError(
                f"Schedule execution references missing rules: {unknown_execution!r}"
            )

        manual_records = list(self._manual.load_all())
        evidence_by_id = {record.evidence_id: record for record in manual_records}
        state_before = self._runtime.current_state()
        links_before = {
            link.source_record_id: link for link in state_before.household.source_links
        }
        pending: list[tuple[ScheduledRule, date, ManualEvidence, bool, bool]] = []
        needs_sync = False
        latest_processed: dict[str, date] = {}

        for rule in sorted(rules, key=lambda item: (item.first_occurrence_date, item.id)):
            if not rule.enabled:
                continue
            execution = execution_by_rule.get(rule.id)
            cursor = execution.last_processed_occurrence_date if execution is not None else None
            occurrence = rule.first_occurrence_date
            while occurrence <= as_of:
                evidence_id = scheduled_occurrence_identity(rule.id, occurrence)
                evidence = evidence_by_id.get(evidence_id)
                evidence_was_new = evidence is None
                if evidence is None:
                    evidence = create_manual_evidence(
                        transaction_type=rule.transaction_type,
                        transaction_date=occurrence,
                        amount=rule.amount,
                        description=rule.description,
                        currency=rule.currency,
                        evidence_id=evidence_id,
                    )
                    evidence_by_id[evidence_id] = evidence
                    manual_records.append(evidence)
                    needs_sync = True
                source_record = manual_evidence_to_source_record(evidence)
                link = links_before.get(source_record.id)
                requires_recovery = link is None
                is_unprocessed = cursor is None or occurrence > cursor
                if requires_recovery:
                    needs_sync = True
                if requires_recovery or is_unprocessed:
                    pending.append(
                        (rule, occurrence, evidence, link is not None, evidence_was_new)
                    )
                    latest_processed[rule.id] = max(
                        occurrence,
                        cursor or occurrence,
                        latest_processed.get(rule.id, occurrence),
                    )
                occurrence = next_monthly_date(occurrence)

        if not pending:
            return ScheduledInputRunResult(())

        self._manual.replace_all(tuple(manual_records))
        sync_result: SourceSyncResult | None = None
        if needs_sync:
            sync_result = self._source_sync.sync_inside_mutation()
        decision_by_source = (
            {decision.source_record_id: decision for decision in sync_result.decisions}
            if sync_result is not None
            else {}
        )
        links_after = {
            link.source_record_id: link
            for link in self._identity.load()
        }

        decisions = self._enrichment.load()
        occurrences: list[ScheduledInputOccurrence] = []
        for rule, occurrence_date, evidence, had_link_before, evidence_was_new in pending:
            source_record = manual_evidence_to_source_record(evidence)
            decision = decision_by_source.get(source_record.id)
            if decision is not None:
                transaction_id = decision.transaction_id
                action = decision.action
            else:
                link = links_after.get(source_record.id)
                if link is None:
                    raise ApplicationStateError(
                        f"Scheduled occurrence {evidence.evidence_id!r} has no Transaction link"
                    )
                transaction_id = link.transaction_id
                action = "recovered" if had_link_before else "reused"
            if rule.note is not None and (evidence_was_new or not had_link_before):
                decisions = update_decision_collection(
                    decisions,
                    transaction_id,
                    note=rule.note,
                )
            occurrences.append(
                ScheduledInputOccurrence(
                    rule_id=rule.id,
                    occurrence_date=occurrence_date,
                    evidence_id=evidence.evidence_id,
                    source_record_id=source_record.id,
                    transaction_id=transaction_id,
                    action=action,
                )
            )
        self._enrichment.replace(decisions)

        next_execution = dict(execution_by_rule)
        latest_occurrence_by_rule: dict[str, ScheduledInputOccurrence] = {}
        for occurrence in occurrences:
            previous = latest_occurrence_by_rule.get(occurrence.rule_id)
            if previous is None or occurrence.occurrence_date > previous.occurrence_date:
                latest_occurrence_by_rule[occurrence.rule_id] = occurrence
        for rule_id, occurrence_date in latest_processed.items():
            latest = latest_occurrence_by_rule[rule_id]
            next_execution[rule_id] = ScheduleExecutionState(
                rule_id=rule_id,
                last_processed_occurrence_date=occurrence_date,
                last_source_record_id=latest.source_record_id,
                last_transaction_id=latest.transaction_id,
                last_action=latest.action,
            )
        self._schedule.replace_execution(
            tuple(next_execution[rule.id] for rule in rules if rule.id in next_execution)
        )
        return ScheduledInputRunResult(tuple(occurrences))

    def _get_rule(self, rule_id: str) -> ScheduledRule:
        rule = next((item for item in self._schedule.load_rules() if item.id == rule_id), None)
        if rule is None:
            raise ApplicationStateError(f"Scheduled Rule {rule_id!r} disappeared after mutation")
        return rule
