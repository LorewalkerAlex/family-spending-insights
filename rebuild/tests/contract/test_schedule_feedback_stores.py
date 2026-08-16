from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from family_spending.domain.feedback import FeedbackContext, FeedbackItem
from family_spending.domain.scheduling import ScheduleExecutionState, ScheduledRule
from family_spending.persistence.filesystem.feedback_store import (
    FeedbackStoreError,
    FilesystemFeedbackStore,
)
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.schedule_store import (
    FilesystemScheduleStore,
    ScheduleStoreError,
)


class ScheduleFeedbackStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.layout = StorageLayout(Path(self.temp_dir.name).resolve())

    def test_schedule_rules_and_execution_round_trip_separately(self) -> None:
        store = FilesystemScheduleStore(self.layout)
        rule = ScheduledRule(
            id="schedule_salary",
            enabled=True,
            transaction_type="income",
            amount=Decimal("15000"),
            description="工资",
            first_occurrence_date=date(2026, 9, 6),
            note="monthly",
        )
        execution = ScheduleExecutionState(
            rule_id=rule.id,
            last_processed_occurrence_date=date(2026, 10, 6),
        )
        store.replace_rules((rule,))
        store.replace_execution((execution,))
        self.assertEqual(store.load_rules(), (rule,))
        self.assertEqual(store.load_execution(), (execution,))

        store.replace_execution(())
        self.assertFalse(self.layout.schedule_execution.exists())
        self.assertEqual(store.load_rules(), (rule,))

    def test_schedule_store_rejects_mixed_or_unknown_schema(self) -> None:
        self.layout.scheduled_rules.parent.mkdir(parents=True, exist_ok=True)
        self.layout.scheduled_rules.write_text(
            json.dumps(
                [
                    {
                        "id": "legacy",
                        "enabled": True,
                        "type": "expense",
                        "amount": "10",
                        "currency": "CNY",
                        "description": "legacy",
                        "first_occurrence_date": "2026-09-01",
                        "note": None,
                        "last_transaction_id": "old-shape",
                    }
                ]
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ScheduleStoreError, "fields"):
            FilesystemScheduleStore(self.layout).load_rules()

    def test_feedback_round_trip_is_strict_and_preserves_utc_context(self) -> None:
        store = FilesystemFeedbackStore(self.layout)
        item = FeedbackItem(
            id="feedback_1",
            created_at=datetime(2026, 8, 16, 1, 2, 3, tzinfo=timezone.utc),
            status="open",
            content="Overview needs polish",
            context=FeedbackContext(
                runtime="desktop_web",
                page="overview",
                entity_type="transaction",
                entity_id="txn_1",
            ),
        )
        store.replace((item,))
        self.assertEqual(store.load(), (item,))

        raw = json.loads(self.layout.feedback.read_text(encoding="utf-8").strip())
        raw["unexpected"] = True
        self.layout.feedback.write_text(
            json.dumps(raw, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(FeedbackStoreError, "fields"):
            store.load()


if __name__ == "__main__":
    unittest.main()
