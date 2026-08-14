from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from family_spending.application import ApplicationPaths
from family_spending.backend.application import RuntimeFamilySpendingApplication
from family_spending.cli import main as cli_main
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    write_transactions_csv,
)
from family_spending.manual_source import (
    create_manual_source_entry,
    read_manual_source_entries,
    write_manual_source_entries,
)
from family_spending.scheduled_input import (
    create_scheduled_input_rule,
    occurrence_source_record_id,
    read_scheduled_input_rules,
    write_scheduled_input_rules,
)


MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
"""


class BackendScheduledJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.paths = ApplicationPaths(
            transactions=root / "transactions.csv",
            manual_source=root / "manual_source_records.jsonl",
            source_links=root / "transaction_source_links.jsonl",
            enrichment_state=root / "enrichment_state.jsonl",
            merchants=root / "merchants.yaml",
            categories=root / "categories.yaml",
            spending_statistics=root / "reports" / "spending_statistics.json",
            emails=root / "emails",
            scheduled_input_rules=root / "scheduled_input_rules.json",
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.emails.mkdir()
        for index, statement_date in enumerate(
            ("2025-12-10", "2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10"),
            start=1,
        ):
            digest = format(index, "024x")
            (self.paths.emails / f"{statement_date}_{digest}.eml").write_bytes(b"test")
        write_transactions_csv(
            (
                CmbTransaction(
                    transaction_id="cmb_food",
                    transaction_date=date(2026, 1, 2),
                    amount=Decimal("20"),
                    description="支付宝-测试餐饮",
                    source_email="statement.eml",
                    source_index=1,
                ),
            ),
            self.paths.transactions,
        )
        self.application = RuntimeFamilySpendingApplication(self.paths)
        self.application.runtime.bootstrap()

    def test_catch_up_materializes_all_occurrences_with_one_source_plan(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("30"),
            description="月度固定支出",
            next_date=date(2026, 1, 11),
            note="月度自动记录",
            rule_id="schedule_monthly",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)

        pipeline = self.application.runtime.pipeline
        with patch.object(
            pipeline,
            "plan_source_sync",
            wraps=pipeline.plan_source_sync,
        ) as plan_source_sync:
            result = self.application.run_due_scheduled_inputs(date(2026, 3, 11))

        self.assertEqual(plan_source_sync.call_count, 1)
        self.assertEqual(
            [item.occurrence_date for item in result.occurrences],
            [date(2026, 1, 11), date(2026, 2, 11), date(2026, 3, 11)],
        )
        self.assertEqual(len(read_manual_source_entries(self.paths.manual_source)), 3)
        persisted_rule = read_scheduled_input_rules(self.paths.scheduled_input_rules)[0]
        self.assertEqual(persisted_rule.next_date, date(2026, 4, 11))
        self.assertEqual(persisted_rule.last_occurrence_date, date(2026, 3, 11))
        self.assertEqual(
            persisted_rule.last_transaction_id,
            result.occurrences[-1].transaction_id,
        )
        self.assertEqual(
            len(self.application.runtime.current_state().manual_entries),
            3,
        )

        second = self.application.run_due_scheduled_inputs(date(2026, 3, 11))
        self.assertEqual(second.occurrences, ())

    def test_submitted_note_updates_existing_transaction_when_occurrence_matches(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("20"),
            description="自动匹配已有消费",
            next_date=date(2026, 1, 2),
            note="Scheduled Note",
            rule_id="schedule_match",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)

        result = self.application.run_due_scheduled_inputs(date(2026, 1, 2))

        self.assertEqual(len(result.occurrences), 1)
        self.assertEqual(result.occurrences[0].action, "matched")
        matched = self.application.get_transaction(result.occurrences[0].transaction_id)
        self.assertEqual(matched.source_record.id, "cmb_food")
        self.assertEqual(matched.enrichment.note, "Scheduled Note")

    def test_existing_occurrence_is_recovered_without_another_source_plan(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("44"),
            description="恢复测试",
            next_date=date(2026, 2, 12),
            rule_id="schedule_recovery",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        source_id = occurrence_source_record_id(rule.id, rule.next_date)
        entry = create_manual_source_entry(
            transaction_type="expense",
            transaction_date=rule.next_date,
            amount=rule.amount,
            description=rule.description,
            source_record_id=source_id,
        )
        write_manual_source_entries((entry,), self.paths.manual_source)
        self.application.runtime.sync_sources()
        expected_transaction_id = next(
            link.transaction_id
            for link in self.application.runtime.current_state().source_links
            if link.source_record_id == source_id
        )

        pipeline = self.application.runtime.pipeline
        with patch.object(
            pipeline,
            "plan_source_sync",
            wraps=pipeline.plan_source_sync,
        ) as plan_source_sync:
            result = self.application.run_due_scheduled_inputs(date(2026, 2, 12))

        self.assertEqual(plan_source_sync.call_count, 0)
        self.assertEqual(result.occurrences[0].action, "recovered")
        self.assertEqual(result.occurrences[0].transaction_id, expected_transaction_id)
        self.assertEqual(len(read_manual_source_entries(self.paths.manual_source)), 1)

    def test_run_due_cli_bootstraps_runtime_without_startup_due_side_effects(self) -> None:
        application = MagicMock()
        application.run_due_scheduled_inputs.return_value.occurrences = ()
        with patch(
            "family_spending.cli.RuntimeFamilySpendingApplication",
            return_value=application,
        ):
            cli_main(["jobs", "run-due", "--as-of", "2026-03-11"])

        application.runtime.bootstrap.assert_called_once_with()
        application.run_due_scheduled_inputs.assert_called_once_with(
            as_of=date(2026, 3, 11)
        )

    def test_failed_batch_restores_rule_manual_and_pipeline_outputs(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("55"),
            description="回滚测试",
            next_date=date(2026, 1, 13),
            rule_id="schedule_rollback",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        financial = self.paths.spending_statistics.with_name("financial_summary.json")
        participants = (
            self.paths.scheduled_input_rules,
            self.paths.manual_source,
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            financial,
        )
        before = {
            path: path.read_bytes() if path.exists() else None
            for path in participants
        }

        with patch.object(
            self.application.runtime.pipeline,
            "write_source_sync_plan",
            side_effect=OSError("scheduled persistence failed"),
        ):
            with self.assertRaisesRegex(OSError, "scheduled persistence failed"):
                self.application.run_due_scheduled_inputs(date(2026, 2, 13))

        for path, contents in before.items():
            if contents is None:
                self.assertFalse(path.exists(), path)
            else:
                self.assertEqual(path.read_bytes(), contents, path)
        self.assertEqual(len(self.application.runtime.current_state().transactions), 1)


if __name__ == "__main__":
    unittest.main()
