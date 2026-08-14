from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.application import (
    ApplicationConflictError,
    ApplicationPaths,
)
from family_spending.backend.application import RuntimeFamilySpendingApplication
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    read_transactions_csv,
    write_transactions_csv,
)
from family_spending.manual_source import read_manual_source_entries


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


def build_paths(root: Path) -> ApplicationPaths:
    paths = ApplicationPaths(
        transactions=root / "transactions.csv",
        manual_source=root / "manual_source_records.jsonl",
        source_links=root / "transaction_source_links.jsonl",
        enrichment_state=root / "enrichment_state.jsonl",
        merchants=root / "merchants.yaml",
        categories=root / "categories.yaml",
        spending_statistics=root / "reports" / "spending_statistics.json",
        emails=root / "emails",
    )
    paths.merchants.write_text(MERCHANTS, encoding="utf-8")
    paths.categories.write_text(CATEGORIES, encoding="utf-8")
    paths.emails.mkdir()
    for index, statement_date in enumerate(
        ("2025-12-10", "2026-01-10", "2026-02-10"),
        start=1,
    ):
        digest = format(index, "024x")
        (paths.emails / f"{statement_date}_{digest}.eml").write_bytes(b"test")
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
        paths.transactions,
    )
    return paths


def file_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


class BackendManualInputCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.paths = build_paths(Path(self.temp_dir.name))
        self.application = RuntimeFamilySpendingApplication(self.paths)
        self.application.initialize()
        self.cmb_transaction_id = self.application.list_transactions()[0].transaction.id
        self.financial_summary = self.paths.spending_statistics.with_name(
            "financial_summary.json"
        )

    def test_create_uses_one_runtime_source_sync_and_refreshes_current_snapshot(self) -> None:
        original_plan = self.application.runtime.pipeline.plan_source_sync
        with (
            patch.object(
                self.application.runtime.pipeline,
                "plan_source_sync",
                wraps=original_plan,
            ) as plan_source_sync,
            patch(
                "family_spending.application.submit_manual_input",
                side_effect=AssertionError("legacy Manual Input command must not run"),
            ),
        ):
            result = self.application.create_manual_input(
                transaction_type="expense",
                transaction_date="2026-01-02",
                amount="20",
                description="支付宝-测试餐饮",
                note="runtime note",
            )

        self.assertEqual(plan_source_sync.call_count, 1)
        self.assertEqual(result.action, "matched")
        self.assertEqual(result.transaction.transaction.id, self.cmb_transaction_id)
        self.assertEqual(result.transaction.enrichment.note, "runtime note")
        snapshot = self.application.runtime.current_state()
        self.assertEqual(len(snapshot.manual_entries), 1)
        self.assertEqual(snapshot.manual_entries[0].id, result.source_record_id)
        self.assertEqual(self.application.list_manual_inputs()[0].source_role, "supporting")

    def test_manual_only_correction_preserves_transaction_identity_and_mapping_following(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="支付宝-测试家电",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(
            transaction_id,
            note="Transaction Workspace Note",
        )

        original_plan = self.application.runtime.pipeline.plan_source_sync
        with (
            patch.object(
                self.application.runtime.pipeline,
                "plan_source_sync",
                wraps=original_plan,
            ) as plan_source_sync,
            patch(
                "family_spending.application.replace_manual_input_command",
                side_effect=AssertionError("legacy correction command must not run"),
            ),
        ):
            corrected = self.application.correct_manual_input(
                created.source_record_id,
                transaction_type="expense",
                transaction_date="2026-01-04",
                amount="42",
                description="支付宝-测试餐饮",
            )

        self.assertEqual(plan_source_sync.call_count, 1)
        self.assertEqual(corrected.manual_input.action, "reused")
        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertEqual(corrected.manual_input.transaction.enrichment.merchant_name, "测试餐饮")
        self.assertEqual(corrected.manual_input.transaction.enrichment.category, "餐饮美食")
        self.assertEqual(
            corrected.manual_input.transaction.enrichment.note,
            "Transaction Workspace Note",
        )
        entries = read_manual_source_entries(self.paths.manual_source)
        self.assertEqual([entry.id for entry in entries], [corrected.manual_input.source_record_id])

    def test_correction_explicit_null_note_clears_current_enrichment_note(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
            note="source note",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(transaction_id, note="workspace note")

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-04",
            amount="40",
            description="现金早餐",
            note=None,
        )

        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertIsNone(corrected.manual_input.transaction.enrichment.note)
        self.assertIsNone(read_manual_source_entries(self.paths.manual_source)[0].note)

    def test_correction_can_converge_manual_transaction_to_existing_cmb_transaction(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-08",
            amount="35.50",
            description="现金早餐",
        )

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="已核对信用卡",
        )

        self.assertEqual(corrected.manual_input.action, "matched")
        self.assertEqual(corrected.manual_input.transaction.transaction.id, self.cmb_transaction_id)
        self.assertEqual(len(self.application.list_transactions()), 1)
        self.assertEqual(corrected.manual_input.transaction.enrichment.note, "已核对信用卡")
        self.assertEqual(self.application.list_manual_inputs()[0].source_role, "supporting")

    def test_correction_preserves_explicit_merchant_exception(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(transaction_id, merchant="测试家电")

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-04",
            amount="42",
            description="支付宝-测试餐饮",
        )

        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertEqual(corrected.manual_input.transaction.enrichment.merchant_name, "测试家电")
        self.assertEqual(corrected.manual_input.transaction.enrichment.default_category, "家居家电")
        self.assertEqual(corrected.manual_input.transaction.enrichment.category, "家居家电")

    def test_correction_preserves_explicit_category_override_when_mapping_merchant_changes(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="支付宝-测试家电",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(transaction_id, category="餐饮美食")

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-04",
            amount="42",
            description="支付宝-测试餐饮",
        )

        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertEqual(corrected.manual_input.transaction.enrichment.merchant_name, "测试餐饮")
        self.assertEqual(corrected.manual_input.transaction.enrichment.default_category, "餐饮美食")
        self.assertEqual(corrected.manual_input.transaction.enrichment.category, "餐饮美食")
        self.assertEqual(
            corrected.manual_input.transaction.enrichment.category_source,
            "manual_override",
        )

    def test_income_create_keeps_source_description_and_bypasses_expense_mapping(self) -> None:
        result = self.application.create_manual_input(
            transaction_type="income",
            transaction_date="2026-01-03",
            amount="1000",
            description="支付宝-测试餐饮",
            note="工资",
        )

        self.assertEqual(result.transaction.transaction.transaction_type, "income")
        self.assertEqual(result.transaction.source_record.description, "支付宝-测试餐饮")
        self.assertIsNone(result.transaction.enrichment.merchant_name)
        self.assertIsNone(result.transaction.enrichment.default_category)
        self.assertEqual(result.transaction.enrichment.category, "其他收入")
        self.assertEqual(result.transaction.enrichment.category_source, "income_default")
        self.assertEqual(result.transaction.enrichment.note, "工资")

    def test_delete_uses_runtime_command_and_removes_unbacked_manual_transaction(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )
        original_plan = self.application.runtime.pipeline.plan_source_sync
        with (
            patch.object(
                self.application.runtime.pipeline,
                "plan_source_sync",
                wraps=original_plan,
            ) as plan_source_sync,
            patch(
                "family_spending.application.delete_manual_input_command",
                side_effect=AssertionError("legacy deletion command must not run"),
            ),
        ):
            deletion = self.application.delete_manual_input(created.source_record_id)

        self.assertEqual(plan_source_sync.call_count, 1)
        self.assertTrue(deletion.transaction_removed)
        self.assertEqual(deletion.transaction_id, created.transaction.transaction.id)
        self.assertFalse(self.paths.manual_source.exists())
        self.assertEqual(len(self.application.list_transactions()), 1)
        self.assertEqual(self.application.list_manual_inputs(), ())

    def test_delete_supporting_manual_input_preserves_cmb_transaction(self) -> None:
        matched = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="manual evidence",
        )

        deletion = self.application.delete_manual_input(matched.source_record_id)

        self.assertFalse(deletion.transaction_removed)
        self.assertEqual(deletion.transaction_id, self.cmb_transaction_id)
        self.assertEqual(len(self.application.list_transactions()), 1)
        self.assertEqual(self.application.list_manual_inputs(), ())
        self.assertEqual(
            self.application.get_transaction(self.cmb_transaction_id).enrichment.note,
            "manual evidence",
        )

    def test_correction_failure_rolls_back_old_source_identity_and_current_state(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
            note="before",
        )
        protected_paths = (
            self.paths.manual_source,
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.financial_summary,
        )
        before = {path: file_bytes(path) for path in protected_paths}

        with patch(
            "family_spending.backend.pipeline.persist_spending_projection",
            side_effect=OSError("correction projection failed"),
        ):
            with self.assertRaisesRegex(OSError, "correction projection failed"):
                self.application.correct_manual_input(
                    created.source_record_id,
                    transaction_type="expense",
                    transaction_date="2026-01-04",
                    amount="42",
                    description="修正早餐",
                    note="after",
                )

        for path in protected_paths:
            self.assertEqual(file_bytes(path), before[path], path.name)
        listed = self.application.list_manual_inputs()
        self.assertEqual([item.entry.id for item in listed], [created.source_record_id])
        self.assertEqual(listed[0].entry.description, "现金早餐")
        self.assertEqual(listed[0].transaction.enrichment.note, "before")

    def test_deletion_failure_rolls_back_manual_source_and_current_state(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )
        protected_paths = (
            self.paths.manual_source,
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.financial_summary,
        )
        before = {path: file_bytes(path) for path in protected_paths}

        with patch(
            "family_spending.backend.pipeline.persist_spending_projection",
            side_effect=OSError("deletion projection failed"),
        ):
            with self.assertRaisesRegex(OSError, "deletion projection failed"):
                self.application.delete_manual_input(created.source_record_id)

        for path in protected_paths:
            self.assertEqual(file_bytes(path), before[path], path.name)
        listed = self.application.list_manual_inputs()
        self.assertEqual([item.entry.id for item in listed], [created.source_record_id])
        self.assertIn(
            created.transaction.transaction.id,
            [item.transaction.id for item in self.application.list_transactions()],
        )

    def test_manual_mutation_failure_rolls_back_source_and_both_projections_without_leaking_failed_state(self) -> None:
        protected_paths = (
            self.paths.manual_source,
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            self.financial_summary,
        )
        before = {path: file_bytes(path) for path in protected_paths}
        snapshot_before = self.application.runtime.current_state()

        with patch(
            "family_spending.backend.pipeline.persist_spending_projection",
            side_effect=OSError("manual projection failed"),
        ):
            with self.assertRaisesRegex(OSError, "manual projection failed"):
                self.application.create_manual_input(
                    transaction_type="expense",
                    transaction_date="2026-01-03",
                    amount="35.50",
                    description="支付宝-测试家电",
                )

        for path in protected_paths:
            self.assertEqual(file_bytes(path), before[path], path.name)
        snapshot_after = self.application.runtime.current_state()
        self.assertEqual(snapshot_after.manual_entries, snapshot_before.manual_entries)
        self.assertEqual(snapshot_after.source_links, snapshot_before.source_links)
        self.assertEqual(snapshot_after.transactions, snapshot_before.transactions)
        self.assertEqual(
            snapshot_after.enrichment_states,
            snapshot_before.enrichment_states,
        )
        self.assertEqual(self.application.list_manual_inputs(), ())

    def test_ambiguous_create_still_surfaces_application_conflict_without_mutation(self) -> None:
        current = read_transactions_csv(self.paths.transactions)
        write_transactions_csv(
            current
            + (
                CmbTransaction(
                    transaction_id="cmb_ambiguous",
                    transaction_date=date(2026, 1, 3),
                    amount=Decimal("20"),
                    description="未知商户",
                    source_email="statement.eml",
                    source_index=2,
                ),
            ),
            self.paths.transactions,
        )
        self.application.initialize()
        before = {
            self.paths.manual_source: file_bytes(self.paths.manual_source),
            self.paths.source_links: file_bytes(self.paths.source_links),
            self.paths.enrichment_state: file_bytes(self.paths.enrichment_state),
            self.paths.spending_statistics: file_bytes(self.paths.spending_statistics),
            self.financial_summary: file_bytes(self.financial_summary),
        }

        with self.assertRaisesRegex(ApplicationConflictError, "multiple existing transactions"):
            self.application.create_manual_input(
                transaction_type="expense",
                transaction_date="2026-01-02",
                amount="20",
                description="手工未知商户",
            )

        for path, contents in before.items():
            self.assertEqual(file_bytes(path), contents, path.name)


if __name__ == "__main__":
    unittest.main()
