from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.application import (
    ApplicationPaths,
    ApplicationStateError,
    ApplicationValidationError,
    FamilySpendingApplication,
)
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    read_transactions_csv,
    write_transactions_csv,
)
from family_spending.manual_input import submit_manual_input
from family_spending.manual_source import create_manual_source_entry
from family_spending.statistics_generation import generate_spending_statistics

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


class ApplicationTests(unittest.TestCase):
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
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.emails.mkdir()
        for index, statement_date in enumerate(
            ("2025-12-10", "2026-01-10", "2026-02-10"),
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
        self.application = FamilySpendingApplication(self.paths)
        self.application.initialize()
        self.transaction_id = self.application.list_transactions()[0].transaction.id

    def test_query_joins_transaction_source_and_current_enrichment(self) -> None:
        view = self.application.get_transaction(self.transaction_id).to_dict()
        self.assertEqual(view["amount"], "20")
        self.assertEqual(view["source"]["description"], "支付宝-测试餐饮")
        self.assertEqual(view["enrichment"]["merchant"], "测试餐饮")
        self.assertEqual(view["enrichment"]["category"], "餐饮美食")
        self.assertEqual(view["enrichment"]["category_source"], "merchant_default")

    def test_enrichment_edit_rebuilds_downstream_without_reconciliation(self) -> None:
        transactions_before = self.paths.transactions.read_bytes()
        links_before = self.paths.source_links.read_bytes()
        self.assertFalse(self.paths.manual_source.exists())
        with (
            patch(
                "family_spending.reconciliation.CmbReconciler.reconcile",
                side_effect=AssertionError("CMB reconciliation must not run"),
            ),
            patch(
                "family_spending.reconciliation.ManualReconciler.reconcile",
                side_effect=AssertionError("Manual reconciliation must not run"),
            ),
        ):
            changed = self.application.update_enrichment(
                self.transaction_id,
                merchant="测试家电",
                note="API 修改",
            )
        self.assertEqual(changed.enrichment.merchant_name, "测试家电")
        self.assertEqual(changed.enrichment.category, "家居家电")
        self.assertEqual(changed.enrichment.category_source, "merchant_default")
        self.assertEqual(changed.enrichment.note, "API 修改")
        self.assertEqual(self.paths.transactions.read_bytes(), transactions_before)
        self.assertEqual(self.paths.source_links.read_bytes(), links_before)
        self.assertFalse(self.paths.manual_source.exists())
        payload = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))
        january = next(month for month in payload["months"] if month["month"] == "2026-01")
        self.assertEqual(january["categories"][0]["category"], "家居家电")

    def test_explicit_category_survives_merchant_change_and_can_reset_to_default(self) -> None:
        self.application.update_enrichment(
            self.transaction_id,
            category="家居家电",
        )
        changed = self.application.update_enrichment(
            self.transaction_id,
            merchant="自定义商户",
        )
        self.assertEqual(changed.enrichment.category, "家居家电")
        self.assertEqual(changed.enrichment.category_source, "manual_override")
        self.assertIsNone(changed.enrichment.default_category)
        reset = self.application.update_enrichment(
            self.transaction_id,
            category=None,
        )
        self.assertEqual(reset.enrichment.category, "待分类")
        self.assertEqual(reset.enrichment.category_source, "unclassified")

    def test_source_reconciliation_reads_current_persisted_merchant_as_evidence(self) -> None:
        self.application.update_enrichment(
            self.transaction_id,
            merchant="测试家电",
        )
        result = submit_manual_input(
            create_manual_source_entry(
                transaction_type="expense",
                transaction_date=date(2026, 1, 2),
                amount=Decimal("20"),
                merchant_name="测试家电",
                source_record_id="manual_matching_current_merchant",
            ),
            transactions_path=self.paths.transactions,
            manual_source_path=self.paths.manual_source,
            source_links_path=self.paths.source_links,
            merchants_path=self.paths.merchants,
            categories_path=self.paths.categories,
            output_path=self.paths.spending_statistics,
            emails_dir=self.paths.emails,
            enrichment_state_path=self.paths.enrichment_state,
        )
        self.assertEqual(result.transaction_id, self.transaction_id)
        self.assertEqual(result.action, "matched")
        self.assertEqual(len(self.application.list_transactions()), 1)

    def test_source_change_requires_initialize_before_client_queries(self) -> None:
        current = read_transactions_csv(self.paths.transactions)
        write_transactions_csv(
            current
            + (
                CmbTransaction(
                    transaction_id="cmb_new",
                    transaction_date=date(2026, 1, 3),
                    amount=Decimal("10"),
                    description="支付宝-测试餐饮",
                    source_email="statement.eml",
                    source_index=2,
                ),
            ),
            self.paths.transactions,
        )
        with self.assertRaisesRegex(ApplicationStateError, "initialize"):
            self.application.list_transactions()
        self.application.initialize()
        self.assertEqual(len(self.application.list_transactions()), 2)

    def test_persisted_edit_is_not_overwritten_by_full_statistics_rebuild(self) -> None:
        self.application.update_enrichment(
            self.transaction_id,
            category="家居家电",
        )
        generate_spending_statistics(
            transactions_path=self.paths.transactions,
            merchants_path=self.paths.merchants,
            categories_path=self.paths.categories,
            output_path=self.paths.spending_statistics,
            emails_dir=self.paths.emails,
            manual_source_path=self.paths.manual_source,
            source_links_path=self.paths.source_links,
            enrichment_state_path=self.paths.enrichment_state,
        )
        current = self.application.get_transaction(self.transaction_id)
        self.assertEqual(current.enrichment.category, "家居家电")
        self.assertEqual(current.enrichment.category_source, "manual_override")

    def test_unknown_category_is_rejected_without_changing_state(self) -> None:
        state_before = self.paths.enrichment_state.read_bytes()
        with self.assertRaisesRegex(ApplicationValidationError, "Unknown category"):
            self.application.update_enrichment(
                self.transaction_id,
                category="不存在分类",
            )
        self.assertEqual(self.paths.enrichment_state.read_bytes(), state_before)

    def test_projection_write_failure_does_not_change_enrichment_state(self) -> None:
        state_before = self.paths.enrichment_state.read_bytes()
        with patch(
            "family_spending.application.write_spending_projection",
            side_effect=OSError("projection write failed"),
        ):
            with self.assertRaisesRegex(OSError, "projection write failed"):
                self.application.update_enrichment(
                    self.transaction_id,
                    category="家居家电",
                )
        self.assertEqual(self.paths.enrichment_state.read_bytes(), state_before)

    def test_enrichment_write_failure_restores_previous_projection(self) -> None:
        state_before = self.paths.enrichment_state.read_bytes()
        projection_before = self.paths.spending_statistics.read_bytes()
        with patch(
            "family_spending.application.write_enrichment_states",
            side_effect=OSError("enrichment write failed"),
        ):
            with self.assertRaisesRegex(OSError, "enrichment write failed"):
                self.application.update_enrichment(
                    self.transaction_id,
                    category="家居家电",
                )
        self.assertEqual(self.paths.enrichment_state.read_bytes(), state_before)
        self.assertEqual(self.paths.spending_statistics.read_bytes(), projection_before)


if __name__ == "__main__":
    unittest.main()
