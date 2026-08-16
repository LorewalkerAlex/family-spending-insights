from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.scheduling import (
    ScheduleExecutionState,
    ScheduledRule,
    next_occurrence_date,
    scheduled_occurrence_identity,
)


class SchedulingTests(unittest.TestCase):
    def rule(self, first: date = date(2026, 8, 15)) -> ScheduledRule:
        return ScheduledRule(
            id="schedule_salary",
            enabled=True,
            transaction_type="income",
            amount=Decimal("1000"),
            description="salary",
            first_occurrence_date=first,
        )

    def test_rule_and_execution_cursor_are_separate(self) -> None:
        rule = self.rule()
        execution = ScheduleExecutionState(
            rule_id=rule.id,
            last_processed_occurrence_date=date(2026, 9, 15),
        )
        self.assertEqual(next_occurrence_date(rule, execution), date(2026, 10, 15))
        self.assertEqual(rule.first_occurrence_date, date(2026, 8, 15))

    def test_rule_date_change_reanchors_future_occurrences_without_rewriting_cursor(self) -> None:
        execution = ScheduleExecutionState(
            rule_id="schedule_salary",
            last_processed_occurrence_date=date(2026, 9, 15),
        )
        rescheduled = self.rule(date(2026, 10, 10))
        self.assertEqual(next_occurrence_date(rescheduled, execution), date(2026, 10, 10))

    def test_occurrence_identity_is_stable_for_retry_and_changes_by_date(self) -> None:
        first = scheduled_occurrence_identity("schedule_salary", date(2026, 8, 15))
        repeated = scheduled_occurrence_identity("schedule_salary", date(2026, 8, 15))
        next_month = scheduled_occurrence_identity("schedule_salary", date(2026, 9, 15))
        self.assertEqual(first, repeated)
        self.assertNotEqual(first, next_month)

    def test_monthly_rule_rejects_days_after_28(self) -> None:
        with self.assertRaisesRegex(DomainInvariantError, "1-28"):
            self.rule(date(2026, 8, 29))


if __name__ == "__main__":
    unittest.main()
