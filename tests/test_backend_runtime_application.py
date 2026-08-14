from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.application import ApplicationPaths, ApplicationStateError
from family_spending.backend.application import RuntimeFamilySpendingApplication
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    read_transactions_csv,
    write_transactions_csv,
)


MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
"""


class BackendRuntimeApplicationTests(unittest.TestCase):
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
        self.application = RuntimeFamilySpendingApplication(self.paths)
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

    def test_transaction_and_review_queries_reuse_one_runtime_snapshot(self) -> None:
        first = self.application.runtime.current_state()
        with (
            patch(
                "family_spending.backend.state.read_transactions_csv",
                side_effect=AssertionError("runtime query must not reread CMB CSV"),
            ),
            patch(
                "family_spending.backend.state.read_manual_source_entries",
                side_effect=AssertionError("runtime query must not reread Manual Source"),
            ),
            patch(
                "family_spending.backend.state.read_transaction_source_links",
                side_effect=AssertionError("runtime query must not reread Source Links"),
            ),
            patch(
                "family_spending.backend.state.read_enrichment_states",
                side_effect=AssertionError("runtime query must not reread Enrichment"),
            ),
            patch(
                "family_spending.backend.state.load_merchant_mappings",
                side_effect=AssertionError("runtime query must not reload Mapping"),
            ),
        ):
            transactions = self.application.list_transactions()
            workspace = self.application.get_mapping_review_workspace()
            categories = self.application.list_categories()
            manual_descriptions = self.application.list_manual_descriptions()
            manual_inputs = self.application.list_manual_inputs()
            second = self.application.runtime.current_state()

        self.assertIs(first, second)
        self.assertEqual(len(transactions), 2)
        self.assertEqual(len(workspace.items), 1)
        self.assertEqual(workspace.items[0].description, "支付宝-待审核")
        self.assertEqual(categories, ("餐饮美食",))
        self.assertEqual(manual_descriptions, ())
        self.assertEqual(manual_inputs, ())

    def test_external_source_change_requires_sync_before_runtime_query(self) -> None:
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

    def test_enrichment_failure_rolls_back_state_and_both_projections(self) -> None:
        known = next(
            view
            for view in self.application.list_transactions()
            if view.source_record.id == "cmb_known"
        )
        financial = self.paths.spending_statistics.with_name("financial_summary.json")
        before = {
            self.paths.enrichment_state: self.paths.enrichment_state.read_bytes(),
            self.paths.spending_statistics: self.paths.spending_statistics.read_bytes(),
            financial: financial.read_bytes(),
        }
        with patch(
            "family_spending.backend.application.persist_spending_projection",
            side_effect=OSError("projection write failed"),
        ):
            with self.assertRaisesRegex(OSError, "projection write failed"):
                self.application.update_enrichment(
                    known.transaction.id,
                    note="should roll back",
                )

        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents, path.name)
        self.assertIsNone(
            self.application.get_transaction(known.transaction.id).enrichment.note
        )

    def test_mapping_apply_refreshes_runtime_without_reconciliation(self) -> None:
        preview = self.application.preview_mapping_review(
            description="支付宝-待审核",
            merchant="测试餐饮",
            category="餐饮美食",
        )
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
            self.application.apply_mapping_review(
                description="支付宝-待审核",
                merchant="测试餐饮",
                category="餐饮美食",
                preview_token=preview.token,
            )

        self.assertEqual(self.application.get_mapping_review_workspace().items, ())
        mapped = next(
            view
            for view in self.application.list_transactions()
            if view.source_record.id == "cmb_unknown"
        )
        self.assertEqual(mapped.enrichment.merchant_name, "测试餐饮")
        self.assertEqual(mapped.enrichment.category, "餐饮美食")

    def test_mapping_failure_rolls_back_mapping_state_and_both_projections(self) -> None:
        preview = self.application.preview_mapping_review(
            description="支付宝-待审核",
            merchant="测试餐饮",
            category="餐饮美食",
        )
        financial = self.paths.spending_statistics.with_name("financial_summary.json")
        paths = (
            self.paths.merchants,
            self.paths.categories,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
            financial,
        )
        before = {path: path.read_bytes() for path in paths}

        with patch(
            "family_spending.backend.application.persist_spending_projection",
            side_effect=OSError("projection write failed"),
        ):
            with self.assertRaisesRegex(OSError, "projection write failed"):
                self.application.apply_mapping_review(
                    description="支付宝-待审核",
                    merchant="测试餐饮",
                    category="餐饮美食",
                    preview_token=preview.token,
                )

        for path in paths:
            self.assertEqual(path.read_bytes(), before[path], path.name)
        self.assertEqual(len(self.application.get_mapping_review_workspace().items), 1)


if __name__ == "__main__":
    unittest.main()
