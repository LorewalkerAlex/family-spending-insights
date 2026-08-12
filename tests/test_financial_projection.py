from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from family_spending.enrichment import (
    INCOME_DEFAULT_CATEGORY,
    TransactionEnrichment,
)
from family_spending.financial_projection import (
    FinancialProjectionError,
    build_financial_projection,
)
from family_spending.source_records import SourceRecord
from family_spending.transactions import Transaction


def transaction(
    transaction_id: str,
    transaction_type: str,
    amount: str,
    when: date,
) -> Transaction:
    return Transaction(
        id=transaction_id,
        transaction_type=transaction_type,  # type: ignore[arg-type]
        transaction_date=when,
        amount=Decimal(amount),
        currency="CNY",
    )


def source(item: Transaction, description: str) -> SourceRecord[None]:
    return SourceRecord(
        id=f"source_{item.id}",
        source_type="manual",
        transaction_type=item.transaction_type,
        transaction_date=item.transaction_date,
        amount=item.amount,
        currency=item.currency,
        description=description,
        provenance=None,
    )


def expense_enrichment(item: Transaction) -> TransactionEnrichment:
    return TransactionEnrichment(
        transaction_id=item.id,
        merchant_name="测试商户",
        display_name="测试商户",
        default_category="餐饮美食",
        category="餐饮美食",
        category_source="merchant_default",
        is_unclassified=False,
        review_signals=(),
    )


def income_enrichment(item: Transaction) -> TransactionEnrichment:
    return TransactionEnrichment(
        transaction_id=item.id,
        merchant_name=None,
        display_name="工资",
        default_category=None,
        category=INCOME_DEFAULT_CATEGORY,
        category_source="income_default",
        is_unclassified=False,
        review_signals=(),
    )


class FinancialProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.emails_dir = Path(self.temp_dir.name) / "emails"
        self.emails_dir.mkdir()

    def add_statement(self, statement_date: str, index: int) -> None:
        digest = format(index, "024x")
        (self.emails_dir / f"{statement_date}_{digest}.eml").write_bytes(b"test")

    def build(self, transactions: tuple[Transaction, ...]):
        sources = {item.id: source(item, item.id) for item in transactions}
        enrichments = {
            item.id: income_enrichment(item)
            if item.transaction_type == "income"
            else expense_enrichment(item)
            for item in transactions
        }
        return build_financial_projection(
            transactions,
            MappingProxyType({item.id: item for item in transactions}),
            MappingProxyType(sources),
            MappingProxyType(enrichments),
            self.emails_dir,
        )

    def test_combines_income_and_net_spending_into_cash_flow(self) -> None:
        self.add_statement("2026-01-10", 1)
        self.add_statement("2026-02-10", 2)
        income = transaction("income", "income", "300", date(2026, 1, 5))
        expense = transaction("expense", "expense", "100", date(2026, 1, 6))

        payload = self.build((income, expense)).payload

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["summary"]["all_data"]["total_income_minor"], 30000)
        self.assertEqual(payload["summary"]["all_data"]["total_spending_minor"], 10000)
        self.assertEqual(payload["summary"]["all_data"]["net_cash_flow_minor"], 20000)
        self.assertEqual(payload["summary"]["shown_data"], payload["summary"]["all_data"])
        month = payload["months"][0]
        self.assertEqual(month["month"], "2026-01")
        self.assertTrue(month["spending_data_complete"])
        self.assertTrue(month["show"])
        self.assertEqual(month["income_transaction_count"], 1)
        self.assertEqual(month["spending_transaction_count"], 1)

    def test_income_only_month_is_kept_but_show_still_uses_spending_coverage_policy(self) -> None:
        income = transaction("income", "income", "500", date(2026, 3, 5))

        payload = self.build((income,)).payload

        self.assertEqual(payload["summary"]["all_data"]["total_income_minor"], 50000)
        self.assertEqual(payload["summary"]["shown_data"]["total_income_minor"], 0)
        month = payload["months"][0]
        self.assertFalse(month["spending_data_complete"])
        self.assertFalse(month["show"])
        self.assertEqual(month["total_spending_minor"], 0)
        self.assertEqual(month["net_cash_flow_minor"], 50000)

    def test_non_positive_income_is_rejected(self) -> None:
        income = transaction("income", "income", "0", date(2026, 1, 5))
        with self.assertRaisesRegex(FinancialProjectionError, "positive amounts"):
            self.build((income,))


if __name__ == "__main__":
    unittest.main()
