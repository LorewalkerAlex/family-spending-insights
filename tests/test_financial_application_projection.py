from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.application import ApplicationPaths, FamilySpendingApplication
from family_spending.ingestion.cmb_email_transactions import CmbTransaction, write_transactions_csv

MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
"""


class FinancialApplicationProjectionTests(unittest.TestCase):
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
        self.financial_summary = self.paths.spending_statistics.with_name(
            "financial_summary.json"
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.emails.mkdir()
        for index, statement_date in enumerate(
            ("2026-01-10", "2026-02-10"),
            start=1,
        ):
            digest = format(index, "024x")
            (self.paths.emails / f"{statement_date}_{digest}.eml").write_bytes(b"test")
        write_transactions_csv(
            (
                CmbTransaction(
                    transaction_id="cmb_purchase",
                    transaction_date=date(2026, 1, 1),
                    amount=Decimal("100"),
                    description="支付宝-测试餐饮",
                    source_email="statement.eml",
                    source_index=1,
                ),
                CmbTransaction(
                    transaction_id="cmb_refund",
                    transaction_date=date(2026, 1, 15),
                    amount=Decimal("-100"),
                    description="退款-未映射",
                    source_email="statement.eml",
                    source_index=2,
                ),
            ),
            self.paths.transactions,
        )
        self.application = FamilySpendingApplication(self.paths)
        self.application.initialize()
        self.refund_transaction_id = next(
            item.transaction.id
            for item in self.application.list_transactions()
            if item.source_record.description == "退款-未映射"
        )
        self.assertEqual(self._financial_spending_minor(), 10_000)

    def _financial_spending_minor(self) -> int:
        """Read shown net spending from the derived household financial sidecar."""
        payload = json.loads(self.financial_summary.read_text(encoding="utf-8"))
        return int(payload["summary"]["shown_data"]["total_spending_minor"])

    def test_transaction_enrichment_refreshes_financial_sidecar_when_refund_matching_changes(self) -> None:
        self.application.update_enrichment(
            self.refund_transaction_id,
            merchant="测试餐饮",
        )
        self.assertEqual(self._financial_spending_minor(), 0)

    def test_mapping_review_refreshes_financial_sidecar_when_refund_matching_changes(self) -> None:
        preview = self.application.preview_mapping_review(
            description="退款-未映射",
            merchant="测试餐饮",
            category="餐饮美食",
        )
        self.application.apply_mapping_review(
            description="退款-未映射",
            merchant="测试餐饮",
            category="餐饮美食",
            preview_token=preview.token,
        )
        self.assertEqual(self._financial_spending_minor(), 0)

    def test_financial_sidecar_failure_restores_spending_and_keeps_enrichment_unchanged(self) -> None:
        spending_before = self.paths.spending_statistics.read_bytes()
        financial_before = self.financial_summary.read_bytes()
        enrichment_before = self.paths.enrichment_state.read_bytes()
        with patch(
            "family_spending.spending_projection.write_financial_projection",
            side_effect=OSError("financial projection write failed"),
        ):
            with self.assertRaisesRegex(OSError, "financial projection write failed"):
                self.application.update_enrichment(
                    self.refund_transaction_id,
                    merchant="测试餐饮",
                )
        self.assertEqual(self.paths.spending_statistics.read_bytes(), spending_before)
        self.assertEqual(self.financial_summary.read_bytes(), financial_before)
        self.assertEqual(self.paths.enrichment_state.read_bytes(), enrichment_before)


if __name__ == "__main__":
    unittest.main()
