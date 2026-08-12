from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_spending.enrichment import INCOME_DEFAULT_CATEGORY, TransactionEnrichmentState
from family_spending.enrichment_store import read_enrichment_states, write_enrichment_states
from family_spending.ingestion.cmb_email_transactions import write_transactions_csv
from family_spending.manual_source import (
    create_manual_source_entry,
    write_manual_source_entries,
)
from family_spending.statistics_generation import generate_spending_statistics

MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
"""


class FinancialStatisticsGenerationTests(unittest.TestCase):
    def test_manual_income_persists_without_mapping_and_generates_financial_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transactions = root / "transactions.csv"
            manual_source = root / "manual_source_records.jsonl"
            source_links = root / "transaction_source_links.jsonl"
            enrichment_state = root / "enrichment_state.jsonl"
            merchants = root / "merchants.yaml"
            categories = root / "categories.yaml"
            emails = root / "emails"
            spending = root / "reports" / "spending_statistics.json"
            financial = root / "reports" / "financial_summary.json"

            emails.mkdir()
            merchants.write_text(MERCHANTS, encoding="utf-8")
            categories.write_text(CATEGORIES, encoding="utf-8")
            write_transactions_csv((), transactions)
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
                manual_source,
            )
            for index, statement_date in enumerate(("2026-01-10", "2026-02-10"), start=1):
                (emails / f"{statement_date}_{format(index, '024x')}.eml").write_bytes(b"test")

            generate_spending_statistics(
                transactions_path=transactions,
                merchants_path=merchants,
                categories_path=categories,
                output_path=spending,
                emails_dir=emails,
                manual_source_path=manual_source,
                source_links_path=source_links,
                enrichment_state_path=enrichment_state,
                financial_output_path=financial,
            )

            spending_payload = json.loads(spending.read_text(encoding="utf-8"))
            financial_payload = json.loads(financial.read_text(encoding="utf-8"))
            states = read_enrichment_states(enrichment_state)

            self.assertEqual(spending_payload["schema_version"], 2)
            self.assertEqual(spending_payload["months"], [])
            self.assertEqual(financial_payload["schema_version"], 1)
            self.assertEqual(
                financial_payload["summary"]["shown_data"]["total_income_minor"],
                3_000_000,
            )
            self.assertEqual(
                financial_payload["summary"]["shown_data"]["total_spending_minor"],
                0,
            )
            self.assertEqual(
                financial_payload["summary"]["shown_data"]["net_cash_flow_minor"],
                3_000_000,
            )
            self.assertEqual(len(states), 1)
            self.assertIsNone(states[0].merchant_name)
            self.assertIsNone(states[0].default_category)
            self.assertEqual(states[0].category, INCOME_DEFAULT_CATEGORY)
            self.assertEqual(states[0].category_source, "income_default")

    def test_rebuild_migrates_implicit_legacy_income_state_without_overwriting_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            transactions = root / "transactions.csv"
            manual_source = root / "manual_source_records.jsonl"
            source_links = root / "transaction_source_links.jsonl"
            enrichment_state = root / "enrichment_state.jsonl"
            merchants = root / "merchants.yaml"
            categories = root / "categories.yaml"
            emails = root / "emails"
            spending = root / "reports" / "spending_statistics.json"
            financial = root / "reports" / "financial_summary.json"

            emails.mkdir()
            merchants.write_text(MERCHANTS, encoding="utf-8")
            categories.write_text(CATEGORIES, encoding="utf-8")
            write_transactions_csv((), transactions)
            write_manual_source_entries(
                (
                    create_manual_source_entry(
                        transaction_type="income",
                        transaction_date=date(2026, 1, 5),
                        amount=Decimal("30000"),
                        description="工资-测试公司",
                        source_record_id="manual_income",
                    ),
                ),
                manual_source,
            )
            for index, statement_date in enumerate(("2026-01-10", "2026-02-10"), start=1):
                (emails / f"{statement_date}_{format(index, '024x')}.eml").write_bytes(b"test")

            generate_spending_statistics(
                transactions_path=transactions,
                merchants_path=merchants,
                categories_path=categories,
                output_path=spending,
                emails_dir=emails,
                manual_source_path=manual_source,
                source_links_path=source_links,
                enrichment_state_path=enrichment_state,
                financial_output_path=financial,
            )
            transaction_id = read_enrichment_states(enrichment_state)[0].transaction_id

            for legacy_state in (
                TransactionEnrichmentState(
                    transaction_id=transaction_id,
                    merchant_name=None,
                    default_category=None,
                    category="待分类",
                    category_source="unclassified",
                    note="保留收入备注",
                ),
                TransactionEnrichmentState(
                    transaction_id=transaction_id,
                    merchant_name="测试餐饮",
                    default_category="餐饮美食",
                    category="餐饮美食",
                    category_source="merchant_default",
                    note="保留收入备注",
                ),
            ):
                with self.subTest(category_source=legacy_state.category_source):
                    write_enrichment_states((legacy_state,), enrichment_state)
                    generate_spending_statistics(
                        transactions_path=transactions,
                        merchants_path=merchants,
                        categories_path=categories,
                        output_path=spending,
                        emails_dir=emails,
                        manual_source_path=manual_source,
                        source_links_path=source_links,
                        enrichment_state_path=enrichment_state,
                        financial_output_path=financial,
                    )
                    migrated = read_enrichment_states(enrichment_state)[0]
                    self.assertIsNone(migrated.merchant_name)
                    self.assertIsNone(migrated.default_category)
                    self.assertEqual(migrated.category, INCOME_DEFAULT_CATEGORY)
                    self.assertEqual(migrated.category_source, "income_default")
                    self.assertEqual(migrated.note, "保留收入备注")



if __name__ == "__main__":
    unittest.main()
