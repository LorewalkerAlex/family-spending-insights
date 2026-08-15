from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

from family_spending.backend.application import (
    ApplicationConflictError,
    ApplicationStateError,
    ApplicationValidationError,
    FamilySpendingApplication,
)
from family_spending.backend.paths import BackendPaths
from family_spending.enrichment_store import read_enrichment_states, write_enrichment_states
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    read_transactions_csv,
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
测试家电:
  - 支付宝-测试家电
"""

CATEGORIES = """\
餐饮美食:
  - 测试餐饮
家居家电:
  - 测试家电
"""


class BackendApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.paths = BackendPaths(
            transactions=root / "transactions.csv",
            manual_source=root / "manual_source_records.jsonl",
            source_links=root / "transaction_source_links.jsonl",
            enrichment_state=root / "enrichment_state.jsonl",
            merchants=root / "merchants.yaml",
            categories=root / "categories.yaml",
            spending_statistics=root / "reports" / "spending_statistics.json",
            financial_summary=root / "reports" / "financial_summary.json",
            emails=root / "emails",
            scheduled_input_rules=root / "scheduled_input_rules.json",
            feedback=root / "feedback.jsonl",
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.emails.mkdir()
        for index, statement_date in enumerate(
            ("2025-12-10", "2026-01-10", "2026-02-10", "2026-03-10"),
            start=1,
        ):
            digest = format(index, "024x")
            (self.paths.emails / f"{statement_date}_{digest}.eml").write_bytes(b"test")
        write_transactions_csv(
            (
                self._transaction(
                    "cmb_known",
                    "20",
                    description="支付宝-测试餐饮",
                    source_index=1,
                ),
                self._transaction(
                    "cmb_unknown",
                    "30",
                    description="支付宝-待审核",
                    source_index=2,
                ),
            ),
            self.paths.transactions,
        )
        self.application = FamilySpendingApplication(self.paths)
        self.application.initialize()

    @staticmethod
    def _transaction(
        transaction_id: str,
        amount: str,
        *,
        description: str,
        source_index: int,
    ) -> CmbTransaction:
        return CmbTransaction(
            transaction_id=transaction_id,
            transaction_date=date(2026, 1, source_index + 1),
            amount=Decimal(amount),
            description=description,
            source_email="statement.eml",
            source_index=source_index,
        )

    def test_queries_share_runtime_snapshot_and_projection_reads_do_not_sync(self) -> None:
        first = self.application.runtime.current_state()
        with (
            patch(
                "family_spending.backend.state.read_transactions_csv",
                side_effect=AssertionError("query must not reread CMB CSV"),
            ),
            patch(
                "family_spending.backend.pipeline.HouseholdPipeline.sync_sources",
                side_effect=AssertionError("projection query must not sync"),
            ),
        ):
            transactions = self.application.list_transactions()
            review = self.application.get_mapping_review_workspace()
            financial = self.application.get_financial_summary()
            spending = self.application.get_spending_statistics()
            second = self.application.runtime.current_state()

        self.assertIs(first, second)
        self.assertEqual(len(transactions), 2)
        self.assertEqual(len(review.items), 1)
        self.assertEqual(financial["schema_version"], 1)
        self.assertEqual(spending["schema_version"], 2)

    def test_external_source_change_requires_sync_before_query(self) -> None:
        current = read_transactions_csv(self.paths.transactions)
        write_transactions_csv(
            current
            + (
                self._transaction(
                    "cmb_external",
                    "10",
                    description="支付宝-测试餐饮",
                    source_index=3,
                ),
            ),
            self.paths.transactions,
        )
        with self.assertRaisesRegex(ApplicationStateError, "sync"):
            self.application.list_transactions()
        self.application.initialize()
        self.assertEqual(len(self.application.list_transactions()), 3)

    def test_manual_create_correct_delete_and_income_semantics_use_runtime_pipeline(self) -> None:
        known = next(
            item
            for item in self.application.list_transactions()
            if item.source_record.id == "cmb_known"
        )
        matched = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="manual evidence",
        )
        self.assertEqual(matched.action, "matched")
        self.assertEqual(matched.transaction.transaction.id, known.transaction.id)
        self.assertEqual(matched.transaction.enrichment.note, "manual evidence")

        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-08",
            amount="35.50",
            description="支付宝-测试家电",
        )
        created_transaction_id = created.transaction.transaction.id
        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-09",
            amount="42",
            description="支付宝-测试餐饮",
        )
        self.assertEqual(
            corrected.manual_input.transaction.transaction.id,
            created_transaction_id,
        )
        self.assertEqual(
            corrected.manual_input.transaction.enrichment.merchant_name,
            "测试餐饮",
        )

        income = self.application.create_manual_input(
            transaction_type="income",
            transaction_date="2026-01-06",
            amount="1000",
            description="工资-测试",
            note="工资",
        )
        self.assertIsNone(income.transaction.enrichment.merchant_name)
        self.assertIsNone(income.transaction.enrichment.default_category)
        self.assertEqual(income.transaction.enrichment.category, "其他收入")
        self.assertEqual(income.transaction.enrichment.category_source, "income_default")
        financial = self.application.get_financial_summary()
        january = next(item for item in financial["months"] if item["month"] == "2026-01")
        self.assertEqual(january["total_income_minor"], 100_000)

        deletion = self.application.delete_manual_input(
            corrected.manual_input.source_record_id
        )
        self.assertTrue(deletion.transaction_removed)
        self.assertNotIn(
            created_transaction_id,
            [item.transaction.id for item in self.application.list_transactions()],
        )
        self.assertEqual(len(read_manual_source_entries(self.paths.manual_source)), 2)

    def test_mapping_and_enrichment_mutations_refresh_runtime_and_roll_back_on_failure(self) -> None:
        preview = self.application.preview_mapping_review(
            description="支付宝-待审核",
            merchant="测试餐饮",
            category="餐饮美食",
        )
        with (
            patch(
                "family_spending.reconciliation.CmbReconciler.reconcile",
                side_effect=AssertionError("mapping apply must not reconcile"),
            ),
            patch(
                "family_spending.reconciliation.ManualReconciler.reconcile",
                side_effect=AssertionError("mapping apply must not reconcile"),
            ),
        ):
            self.application.apply_mapping_review(
                description="支付宝-待审核",
                merchant="测试餐饮",
                category="餐饮美食",
                preview_token=preview.token,
            )
        self.assertEqual(self.application.get_mapping_review_workspace().items, ())

        target = next(
            item
            for item in self.application.list_transactions()
            if item.source_record.id == "cmb_unknown"
        )
        paths = (
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.paths.financial_summary,
        )
        before = {path: path.read_bytes() for path in paths}
        with patch(
            "family_spending.backend.application.persist_spending_projection",
            side_effect=OSError("projection write failed"),
        ):
            with self.assertRaisesRegex(OSError, "projection write failed"):
                self.application.update_enrichment(
                    target.transaction.id,
                    note="should roll back",
                )
        for path in paths:
            self.assertEqual(path.read_bytes(), before[path])
        self.assertIsNone(
            self.application.get_transaction(target.transaction.id).enrichment.note
        )

    def test_scheduled_job_batches_due_occurrences_and_rule_crud_stays_in_application(self) -> None:
        assert self.paths.scheduled_input_rules is not None
        due_rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("30"),
            description="月度固定支出",
            next_date=date(2026, 1, 11),
            note="自动记录",
            rule_id="schedule_due",
        )
        write_scheduled_input_rules((due_rule,), self.paths.scheduled_input_rules)
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
        persisted = read_scheduled_input_rules(self.paths.scheduled_input_rules)[0]
        self.assertEqual(persisted.next_date, date(2026, 4, 11))

        future = self.application.create_scheduled_input(
            transaction_type="income",
            amount="15000",
            description="工资",
            next_date="2099-01-06",
            enabled=True,
        )
        updated = self.application.update_scheduled_input(
            future.id,
            transaction_type="income",
            amount="16000",
            description="工资",
            next_date="2099-02-06",
            enabled=False,
        )
        self.assertEqual(updated.amount, Decimal("16000"))
        self.assertFalse(updated.enabled)
        self.assertEqual(self.application.delete_scheduled_input(updated.id).id, updated.id)



    def test_scheduled_recovery_reuses_existing_occurrence_without_source_plan(self) -> None:
        assert self.paths.scheduled_input_rules is not None
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

    def test_scheduled_batch_failure_restores_rule_manual_and_pipeline_outputs(self) -> None:
        assert self.paths.scheduled_input_rules is not None
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("55"),
            description="回滚测试",
            next_date=date(2026, 1, 13),
            rule_id="schedule_rollback",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        participants = (
            self.paths.scheduled_input_rules,
            self.paths.manual_source,
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.paths.financial_summary,
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
        for path in participants:
            actual = path.read_bytes() if path.exists() else None
            self.assertEqual(actual, before[path], path.name)

    def test_manual_correction_preserves_explicit_enrichment_and_can_converge_to_cmb(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-08",
            amount="35.50",
            description="现金早餐",
            note="source note",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(
            transaction_id,
            merchant="测试家电",
            category="餐饮美食",
            note="workspace note",
        )
        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-09",
            amount="42",
            description="支付宝-测试餐饮",
        )
        enrichment = corrected.manual_input.transaction.enrichment
        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertEqual(enrichment.merchant_name, "测试家电")
        self.assertEqual(enrichment.category, "餐饮美食")
        self.assertEqual(enrichment.category_source, "manual_override")
        self.assertEqual(enrichment.note, "workspace note")

        cleared = self.application.correct_manual_input(
            corrected.manual_input.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note=None,
        )
        self.assertEqual(cleared.manual_input.action, "matched")
        known_id = next(
            item.transaction.id
            for item in self.application.list_transactions()
            if item.source_record.id == "cmb_known"
        )
        self.assertEqual(cleared.manual_input.transaction.transaction.id, known_id)
        self.assertIsNone(cleared.manual_input.transaction.enrichment.note)

    def test_delete_supporting_manual_source_preserves_backed_transaction(self) -> None:
        known_id = next(
            item.transaction.id
            for item in self.application.list_transactions()
            if item.source_record.id == "cmb_known"
        )
        matched = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="manual evidence",
        )
        deletion = self.application.delete_manual_input(matched.source_record_id)
        self.assertFalse(deletion.transaction_removed)
        self.assertEqual(deletion.transaction_id, known_id)
        self.assertEqual(
            self.application.get_transaction(known_id).enrichment.note,
            "manual evidence",
        )

    def test_mapping_review_preserves_exceptions_and_requires_fresh_confirmed_preview(self) -> None:
        target = next(
            item
            for item in self.application.list_transactions()
            if item.source_record.id == "cmb_known"
        )
        states = read_enrichment_states(self.paths.enrichment_state)
        write_enrichment_states(
            tuple(
                replace(
                    state,
                    category="家居家电",
                    category_source="transaction_override",
                )
                if state.transaction_id == target.transaction.id
                else state
                for state in states
            ),
            self.paths.enrichment_state,
        )
        self.application.runtime.refresh()

        preview = self.application.preview_mapping_review(
            description="支付宝-待审核",
            merchant="新建商户",
            category="餐饮美食",
        )
        self.assertTrue(preview.is_new_merchant)
        with self.assertRaisesRegex(
            ApplicationValidationError,
            "confirm_new_merchant",
        ):
            self.application.apply_mapping_review(
                description="支付宝-待审核",
                merchant="新建商户",
                category="餐饮美食",
                preview_token=preview.token,
            )
        with self.assertRaisesRegex(ApplicationConflictError, "changed after preview"):
            self.application.apply_mapping_review(
                description="支付宝-待审核",
                merchant="新建商户",
                category="餐饮美食",
                preview_token="0" * 64,
                confirm_new_merchant=True,
            )
        self.application.apply_mapping_review(
            description="支付宝-待审核",
            merchant="新建商户",
            category="餐饮美食",
            preview_token=preview.token,
            confirm_new_merchant=True,
        )
        merchant_data = yaml.safe_load(self.paths.merchants.read_text(encoding="utf-8"))
        self.assertEqual(merchant_data["新建商户"], ["支付宝-待审核"])
        refreshed_target = self.application.get_transaction(target.transaction.id)
        self.assertEqual(refreshed_target.enrichment.category, "家居家电")
        self.assertEqual(
            refreshed_target.enrichment.category_source,
            "transaction_override",
        )

    def test_manual_command_failure_rolls_back_source_runtime_and_both_projections(self) -> None:
        protected = (
            self.paths.manual_source,
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.paths.financial_summary,
        )
        before = {
            path: path.read_bytes() if path.exists() else None
            for path in protected
        }
        snapshot_before = self.application.runtime.current_state()
        with patch(
            "family_spending.backend.pipeline.persist_spending_projection",
            side_effect=OSError("manual projection failed"),
        ):
            with self.assertRaisesRegex(OSError, "manual projection failed"):
                self.application.create_manual_input(
                    transaction_type="expense",
                    transaction_date="2026-01-08",
                    amount="35.50",
                    description="支付宝-测试家电",
                )
        for path in protected:
            actual = path.read_bytes() if path.exists() else None
            self.assertEqual(actual, before[path], path.name)
        snapshot_after = self.application.runtime.current_state()
        self.assertEqual(snapshot_after.manual_entries, snapshot_before.manual_entries)
        self.assertEqual(snapshot_after.source_links, snapshot_before.source_links)
        self.assertEqual(snapshot_after.transactions, snapshot_before.transactions)

    def test_feedback_lifecycle_is_local_product_state(self) -> None:
        item = self.application.create_feedback(
            content="Overview needs polish",
            context={"runtime": "desktop_web", "page": "overview"},
        )
        self.assertEqual(item.status, "open")
        self.assertEqual(self.application.list_feedback()[0].id, item.id)
        resolved = self.application.update_feedback(item.id, status="resolved")
        self.assertEqual(resolved.status, "resolved")


if __name__ == "__main__":
    unittest.main()
