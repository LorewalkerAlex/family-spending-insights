from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_spending.backend.paths import BackendPaths
from family_spending.backend.pipeline import HouseholdPipeline
from family_spending.enrichment import INCOME_DEFAULT_CATEGORY, TransactionEnrichmentState
from family_spending.enrichment_store import read_enrichment_states, write_enrichment_states
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    write_transactions_csv,
)
from family_spending.manual_source import (
    create_manual_source_entry,
    write_manual_source_entries,
)


MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
测试家电:
  - 支付宝-测试家电
  - 退款-支付宝-测试家电
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
家居家电:
  - 测试家电
"""


def transaction(
    transaction_id: str,
    amount: str,
    *,
    transaction_date: date,
    description: str,
    source_index: int,
) -> CmbTransaction:
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        amount=Decimal(amount),
        description=description,
        source_email="statement.eml",
        source_index=source_index,
    )


class BackendPipelineIntegrationTests(unittest.TestCase):
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
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.emails.mkdir()
        for index, statement_date in enumerate(
            ("2025-12-10", "2026-01-10", "2026-02-10"),
            start=1,
        ):
            (self.paths.emails / f"{statement_date}_{format(index, '024x')}.eml").write_bytes(
                b"test"
            )
        self.pipeline = HouseholdPipeline(self.paths)

    def test_source_sync_preserves_transaction_override_and_builds_both_projections(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_purchase",
                    "3000",
                    transaction_date=date(2025, 12, 1),
                    description="支付宝-测试家电",
                    source_index=1,
                ),
                transaction(
                    "cmb_refund",
                    "-1000",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-测试家电",
                    source_index=2,
                ),
                transaction(
                    "cmb_food",
                    "20",
                    transaction_date=date(2026, 1, 2),
                    description="支付宝-测试餐饮",
                    source_index=3,
                ),
                transaction(
                    "cmb_unknown",
                    "30",
                    transaction_date=date(2026, 1, 3),
                    description="支付宝-未知商户",
                    source_index=4,
                ),
            ),
            self.paths.transactions,
        )
        self.pipeline.sync_sources()
        states = read_enrichment_states(self.paths.enrichment_state)
        purchase_transaction_id = next(
            json.loads(line)["transaction_id"]
            for line in self.paths.source_links.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["source_record_id"] == "cmb_purchase"
        )
        write_enrichment_states(
            tuple(
                replace(
                    state,
                    category="餐饮美食",
                    category_source="transaction_override",
                )
                if state.transaction_id == purchase_transaction_id
                else state
                for state in states
            ),
            self.paths.enrichment_state,
        )

        summary = self.pipeline.sync_sources()
        spending = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))
        financial = json.loads(self.paths.financial_summary.read_text(encoding="utf-8"))
        persisted = read_enrichment_states(self.paths.enrichment_state)

        self.assertEqual(summary.raw_transactions, 4)
        self.assertEqual(summary.refund_transactions, 1)
        self.assertEqual(summary.partially_refunded_transactions, 1)
        self.assertEqual(summary.net_consumption_transactions, 3)
        self.assertEqual(summary.unclassified_net_transactions, 1)
        self.assertEqual(summary.total_net_spending, Decimal("2050"))
        self.assertEqual(summary.shown_net_spending, Decimal("2050"))
        self.assertEqual(spending["schema_version"], 2)
        self.assertEqual(financial["schema_version"], 1)
        self.assertIn(
            "transaction_override",
            {state.category_source for state in persisted},
        )

    def test_source_sync_reports_zero_amount_and_same_merchant_refund_fallback(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_zero",
                    "0",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-测试餐饮",
                    source_index=1,
                ),
                transaction(
                    "cmb_purchase",
                    "100",
                    transaction_date=date(2026, 1, 2),
                    description="支付宝-测试家电",
                    source_index=2,
                ),
                transaction(
                    "cmb_refund",
                    "-100",
                    transaction_date=date(2026, 1, 20),
                    description="退款-支付宝-测试家电",
                    source_index=3,
                ),
            ),
            self.paths.transactions,
        )
        summary = self.pipeline.sync_sources()
        spending = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))

        self.assertEqual(summary.zero_amount_transactions, 1)
        self.assertEqual(summary.same_merchant_refund_matches, 1)
        self.assertEqual(summary.same_merchant_matched_amount, Decimal("100"))
        self.assertEqual(summary.net_consumption_transactions, 0)
        self.assertEqual(summary.unmatched_refund_count, 0)
        self.assertEqual(spending["months"], [])

    def test_income_defaults_and_legacy_implicit_income_state_migrate_in_formal_pipeline(self) -> None:
        write_transactions_csv((), self.paths.transactions)
        write_manual_source_entries(
            (
                create_manual_source_entry(
                    transaction_type="income",
                    transaction_date=date(2026, 1, 5),
                    amount=Decimal("30000"),
                    description="工资-测试公司",
                    note="一月工资",
                    source_record_id="manual_income",
                ),
            ),
            self.paths.manual_source,
        )
        self.pipeline.sync_sources()
        state = read_enrichment_states(self.paths.enrichment_state)[0]
        self.assertIsNone(state.merchant_name)
        self.assertIsNone(state.default_category)
        self.assertEqual(state.category, INCOME_DEFAULT_CATEGORY)
        self.assertEqual(state.category_source, "income_default")

        for legacy in (
            TransactionEnrichmentState(
                transaction_id=state.transaction_id,
                merchant_name=None,
                default_category=None,
                category="待分类",
                category_source="unclassified",
                note="保留收入备注",
            ),
            TransactionEnrichmentState(
                transaction_id=state.transaction_id,
                merchant_name="测试餐饮",
                default_category="餐饮美食",
                category="餐饮美食",
                category_source="merchant_default",
                note="保留收入备注",
            ),
        ):
            with self.subTest(category_source=legacy.category_source):
                write_enrichment_states((legacy,), self.paths.enrichment_state)
                self.pipeline.sync_sources()
                migrated = read_enrichment_states(self.paths.enrichment_state)[0]
                self.assertIsNone(migrated.merchant_name)
                self.assertIsNone(migrated.default_category)
                self.assertEqual(migrated.category, INCOME_DEFAULT_CATEGORY)
                self.assertEqual(migrated.category_source, "income_default")
                self.assertEqual(migrated.note, "保留收入备注")

        financial = json.loads(self.paths.financial_summary.read_text(encoding="utf-8"))
        self.assertEqual(
            financial["summary"]["shown_data"]["total_income_minor"],
            3_000_000,
        )
        self.assertEqual(
            financial["summary"]["shown_data"]["net_cash_flow_minor"],
            3_000_000,
        )


if __name__ == "__main__":
    unittest.main()
