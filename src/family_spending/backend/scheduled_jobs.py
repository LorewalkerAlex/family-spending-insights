from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from family_spending.backend.runtime import BackendRuntime
from family_spending.infrastructure.file_uow import (
    FileUnitOfWork,
    FileUnitOfWorkRollbackError,
)
from family_spending.manual_source import (
    create_manual_source_entry,
    write_manual_source_entries,
)
from family_spending.scheduled_input import (
    ScheduledInputError,
    ScheduledInputOccurrence,
    ScheduledInputRollbackError,
    ScheduledInputRunResult,
    next_monthly_date,
    occurrence_source_record_id,
    read_scheduled_input_rules,
    write_scheduled_input_rules,
)


@dataclass(frozen=True)
class _PendingOccurrence:
    rule_id: str
    rule_index: int
    occurrence_date: date
    source_record_id: str
    recovered_transaction_id: str | None = None


class ScheduledInputJobRunner:
    """Materialize all due Scheduled Input occurrences through one Source sync.

    Scheduled rules remain orchestration state. Newly due occurrences are accumulated as
    ordinary Manual Source records, reconciled together once, and then committed with the
    final rule cursor in one file-backed unit of work.
    """

    def __init__(self, runtime: BackendRuntime, rules_path: Path) -> None:
        self.runtime = runtime
        self.rules_path = rules_path

    def run_due(self, as_of: date) -> ScheduledInputRunResult:
        """Catch up every enabled rule through `as_of` without one full pipeline per month."""
        rules = list(read_scheduled_input_rules(self.rules_path))
        if not any(rule.enabled and rule.next_date <= as_of for rule in rules):
            return ScheduledInputRunResult(occurrences=())

        snapshot = self.runtime.current_state()
        manual_entries = list(snapshot.manual_entries)
        entries_by_id = {entry.id: entry for entry in manual_entries}
        links_by_source = {link.source_record_id: link for link in snapshot.source_links}

        pending: list[_PendingOccurrence] = []
        submitted_source_ids: list[str] = []
        ordered_ids = [
            rule.id
            for rule in sorted(rules, key=lambda item: (item.next_date, item.id))
        ]
        for rule_id in ordered_ids:
            rule_index = next(
                index for index, rule in enumerate(rules) if rule.id == rule_id
            )
            rule = rules[rule_index]
            while rule.enabled and rule.next_date <= as_of:
                occurrence_date = rule.next_date
                source_record_id = occurrence_source_record_id(
                    rule.id,
                    occurrence_date,
                )
                existing_entry = entries_by_id.get(source_record_id)
                recovered_transaction_id: str | None = None
                if existing_entry is None:
                    entry = create_manual_source_entry(
                        transaction_type=rule.transaction_type,
                        transaction_date=occurrence_date,
                        amount=rule.amount,
                        description=rule.description,
                        note=rule.note,
                        currency=rule.currency,
                        source_record_id=source_record_id,
                    )
                    manual_entries.append(entry)
                    entries_by_id[source_record_id] = entry
                    submitted_source_ids.append(source_record_id)
                else:
                    link = links_by_source.get(source_record_id)
                    if link is None:
                        raise ScheduledInputError(
                            f"Scheduled occurrence {source_record_id!r} exists without "
                            "a Transaction link"
                        )
                    recovered_transaction_id = link.transaction_id

                pending.append(
                    _PendingOccurrence(
                        rule_id=rule.id,
                        rule_index=rule_index,
                        occurrence_date=occurrence_date,
                        source_record_id=source_record_id,
                        recovered_transaction_id=recovered_transaction_id,
                    )
                )
                rule = replace(
                    rule,
                    next_date=next_monthly_date(occurrence_date),
                )
                rules[rule_index] = rule

        plan = None
        decisions_by_source = {}
        if submitted_source_ids:
            plan = self.runtime.pipeline.plan_source_sync(
                manual_entries=tuple(manual_entries),
                submitted_source_ids=tuple(submitted_source_ids),
            )
            decisions_by_source = {
                decision.source_record_id: decision
                for decision in plan.decisions
            }

        occurrences: list[ScheduledInputOccurrence] = []
        for item in pending:
            if item.recovered_transaction_id is not None:
                transaction_id = item.recovered_transaction_id
                action = "recovered"
            else:
                decision = decisions_by_source.get(item.source_record_id)
                if decision is None:
                    raise ScheduledInputError(
                        "Scheduled occurrence was not present in the Source sync decision set: "
                        f"{item.source_record_id!r}"
                    )
                transaction_id = decision.transaction_id
                action = decision.action

            occurrence = ScheduledInputOccurrence(
                rule_id=item.rule_id,
                occurrence_date=item.occurrence_date,
                source_record_id=item.source_record_id,
                transaction_id=transaction_id,
                action=action,
            )
            occurrences.append(occurrence)
            current_rule = rules[item.rule_index]
            rules[item.rule_index] = replace(
                current_rule,
                last_occurrence_date=item.occurrence_date,
                last_source_record_id=item.source_record_id,
                last_transaction_id=transaction_id,
                last_action=action,
            )

        persisted_paths = [self.rules_path]
        if plan is not None:
            persisted_paths.extend(
                (
                    self.runtime.paths.manual_source,
                    *self.runtime.pipeline.source_sync_persisted_paths(),
                )
            )

        try:
            with FileUnitOfWork(
                persisted_paths,
                label="Scheduled Input due run",
            ) as unit_of_work:
                if plan is not None:
                    write_manual_source_entries(
                        tuple(manual_entries),
                        self.runtime.paths.manual_source,
                    )
                    self.runtime.pipeline.write_source_sync_plan(plan)
                write_scheduled_input_rules(tuple(rules), self.rules_path)
                if plan is not None:
                    self.runtime.refresh()
                unit_of_work.commit()
        except FileUnitOfWorkRollbackError as exc:
            raise ScheduledInputRollbackError(str(exc)) from exc

        return ScheduledInputRunResult(occurrences=tuple(occurrences))
