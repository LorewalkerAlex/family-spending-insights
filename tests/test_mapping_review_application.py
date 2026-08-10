from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import yaml

from family_spending.application import (
    ApplicationConflictError,
    ApplicationPaths,
    ApplicationValidationError,
    FamilySpendingApplication,
)
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    write_transactions_csv,
)


MERCHANTS = """\
餐饮基准:
  - 支付宝-餐饮基准
目标商户:
  - 支付宝-目标已知
其他购物:
  - 支付宝-其他购物
医疗商户:
  - 支付宝-医疗商户
"""

CATEGORIES = """\
餐饮美食:
  - 餐饮基准
综合购物:
  - 目标商户
  - 其他购物
医疗健康:
  - 医疗商户
"""

OVERRIDES = """\
{"transaction_id":"cmb_target","category":"医疗健康","note":"历史人工例外"}
"""


class MappingReviewApplicationTests(unittest.TestCase):
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
            overrides=root / "transaction_category_overrides.jsonl",
            spending_statistics=root / "reports" / "spending_statistics.json",
            emails=root / "emails",
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.overrides.write_text(OVERRIDES, encoding="utf-8")
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
                    transaction_id="cmb_unknown_1",
                    transaction_date=date(2026, 1, 2),
                    amount=Decimal("20"),
                    description="支付宝-待审核商户",
                    source_email="statement.eml",
                    source_index=1,
                ),
                CmbTransaction(
                    transaction_id="cmb_unknown_2",
                    transaction_date=date(2026, 1, 3),
                    amount=Decimal("30"),
                    description="支付宝-待审核商户",
                    source_email="statement.eml",
                    source_index=2,
                ),
                CmbTransaction(
                    transaction_id="cmb_target",
                    transaction_date=date(2026, 1, 4),
                    amount=Decimal("40"),
                    description="支付宝-目标已知",
                    source_email="statement.eml",
                    source_index=3,
                ),
            ),
            self.paths.transactions,
        )
        self.application = FamilySpendingApplication(self.paths)
        self.application.initialize()

    def _transactions_for_description(self, description: str):
        return sorted(
            (
                view
                for view in self.application.list_transactions()
                if view.source_record.description == description
            ),
            key=lambda view: view.transaction.transaction_date,
        )

    def test_workspace_aggregates_unmapped_description_across_transactions(self) -> None:
        workspace = self.application.get_mapping_review_workspace()
        self.assertEqual(len(workspace.items), 1)
        item = workspace.items[0]
        self.assertEqual(item.description, "支付宝-待审核商户")
        self.assertEqual(item.transaction_count, 2)
        self.assertEqual(item.total_amount, Decimal("50"))
        self.assertEqual(item.currency, "CNY")
        self.assertEqual(item.transaction_only_exception_count, 0)
        self.assertIn("目标商户", {merchant.name for merchant in workspace.merchants})
        self.assertIn("餐饮美食", workspace.categories)

    def test_apply_propagates_mapping_without_overwriting_transaction_exceptions(self) -> None:
        unknown = self._transactions_for_description("支付宝-待审核商户")
        manual_exception_id = unknown[1].transaction.id
        self.application.update_enrichment(
            manual_exception_id,
            merchant="单笔例外商户",
        )
        transactions_before = self.paths.transactions.read_bytes()
        links_before = self.paths.source_links.read_bytes()

        preview = self.application.preview_mapping_review(
            description="支付宝-待审核商户",
            merchant="目标商户",
            category="餐饮美食",
        )
        self.assertFalse(preview.is_new_merchant)
        self.assertEqual(preview.previous_default_category, "综合购物")
        self.assertEqual(preview.description_transaction_count, 2)
        self.assertEqual(preview.description_affected_transaction_count, 1)
        self.assertEqual(preview.default_category_affected_transaction_count, 1)
        self.assertEqual(preview.total_affected_transaction_count, 2)
        self.assertEqual(preview.preserved_merchant_exception_count, 1)
        self.assertEqual(preview.preserved_category_exception_count, 1)

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
            applied = self.application.apply_mapping_review(
                description="支付宝-待审核商户",
                merchant="目标商户",
                category="餐饮美食",
                preview_token=preview.token,
            )
        self.assertEqual(applied.token, preview.token)
        self.assertEqual(self.paths.transactions.read_bytes(), transactions_before)
        self.assertEqual(self.paths.source_links.read_bytes(), links_before)

        merchant_data = yaml.safe_load(self.paths.merchants.read_text(encoding="utf-8"))
        category_data = yaml.safe_load(self.paths.categories.read_text(encoding="utf-8"))
        self.assertIn("支付宝-待审核商户", merchant_data["目标商户"])
        self.assertIn("目标商户", category_data["餐饮美食"])
        self.assertNotIn("目标商户", category_data["综合购物"])

        unknown_after = self._transactions_for_description("支付宝-待审核商户")
        mapped = next(
            view for view in unknown_after if view.transaction.id != manual_exception_id
        )
        manual_exception = next(
            view for view in unknown_after if view.transaction.id == manual_exception_id
        )
        self.assertEqual(mapped.enrichment.merchant_name, "目标商户")
        self.assertEqual(mapped.enrichment.default_category, "餐饮美食")
        self.assertEqual(mapped.enrichment.category, "餐饮美食")
        self.assertEqual(mapped.enrichment.category_source, "merchant_default")
        self.assertEqual(manual_exception.enrichment.merchant_name, "单笔例外商户")
        self.assertIsNone(manual_exception.enrichment.default_category)
        self.assertEqual(manual_exception.enrichment.category, "待分类")

        target = self._transactions_for_description("支付宝-目标已知")[0]
        self.assertEqual(target.enrichment.default_category, "餐饮美食")
        self.assertEqual(target.enrichment.category, "医疗健康")
        self.assertEqual(target.enrichment.category_source, "transaction_override")
        self.assertEqual(self.application.get_mapping_review_workspace().items, ())

    def test_new_merchant_requires_explicit_confirmation(self) -> None:
        merchants_before = self.paths.merchants.read_bytes()
        categories_before = self.paths.categories.read_bytes()
        preview = self.application.preview_mapping_review(
            description="支付宝-待审核商户",
            merchant="新建商户",
            category="餐饮美食",
        )
        self.assertTrue(preview.is_new_merchant)
        with self.assertRaisesRegex(ApplicationValidationError, "confirm_new_merchant"):
            self.application.apply_mapping_review(
                description="支付宝-待审核商户",
                merchant="新建商户",
                category="餐饮美食",
                preview_token=preview.token,
            )
        self.assertEqual(self.paths.merchants.read_bytes(), merchants_before)
        self.assertEqual(self.paths.categories.read_bytes(), categories_before)

        self.application.apply_mapping_review(
            description="支付宝-待审核商户",
            merchant="新建商户",
            category="餐饮美食",
            preview_token=preview.token,
            confirm_new_merchant=True,
        )
        merchant_data = yaml.safe_load(self.paths.merchants.read_text(encoding="utf-8"))
        category_data = yaml.safe_load(self.paths.categories.read_text(encoding="utf-8"))
        self.assertEqual(merchant_data["新建商户"], ["支付宝-待审核商户"])
        self.assertIn("新建商户", category_data["餐饮美食"])

    def test_stale_preview_token_rejects_without_mutation(self) -> None:
        merchants_before = self.paths.merchants.read_bytes()
        categories_before = self.paths.categories.read_bytes()
        with self.assertRaisesRegex(ApplicationConflictError, "changed after preview"):
            self.application.apply_mapping_review(
                description="支付宝-待审核商户",
                merchant="目标商户",
                category="综合购物",
                preview_token="0" * 64,
            )
        self.assertEqual(self.paths.merchants.read_bytes(), merchants_before)
        self.assertEqual(self.paths.categories.read_bytes(), categories_before)

    def test_failure_rolls_back_mapping_enrichment_and_projection_files(self) -> None:
        preview = self.application.preview_mapping_review(
            description="支付宝-待审核商户",
            merchant="目标商户",
            category="综合购物",
        )
        paths = (
            self.paths.merchants,
            self.paths.categories,
            self.paths.enrichment_state,
            self.paths.spending_statistics,
        )
        before = {path: path.read_bytes() for path in paths}
        with patch(
            "family_spending.application.write_spending_projection",
            side_effect=OSError("projection write failed"),
        ):
            with self.assertRaisesRegex(OSError, "projection write failed"):
                self.application.apply_mapping_review(
                    description="支付宝-待审核商户",
                    merchant="目标商户",
                    category="综合购物",
                    preview_token=preview.token,
                )
        for path in paths:
            self.assertEqual(path.read_bytes(), before[path], path.name)


if __name__ == "__main__":
    unittest.main()