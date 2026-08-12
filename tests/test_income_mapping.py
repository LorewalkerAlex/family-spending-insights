from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from family_spending.enrichment import (
    INCOME_DEFAULT_CATEGORY,
    TransactionEnrichmentState,
    enrichment_state_from_result,
)
from family_spending.enrichment_store import read_enrichment_states, write_enrichment_states
from family_spending.mapping import MappingEnrichmentResolver, MerchantMappings
from family_spending.mapping_review import build_mapping_review_items
from family_spending.source_records import SourceRecord
from family_spending.transactions import Transaction


def tx(transaction_id: str, transaction_type: str, amount: str) -> Transaction:
    return Transaction(
        id=transaction_id,
        transaction_type=transaction_type,  # type: ignore[arg-type]
        transaction_date=date(2026, 1, 5),
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


class IncomeMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.gettempdir())
        self.mappings = MerchantMappings(
            description_to_merchant=MappingProxyType({"同一个描述": "消费商户"}),
            merchant_to_category=MappingProxyType({"消费商户": "餐饮美食"}),
            categories=frozenset({"餐饮美食"}),
            merchants_path=root / "merchants.yaml",
            categories_path=root / "categories.yaml",
        )

    def test_income_bypasses_matching_expense_merchant_mapping(self) -> None:
        item = tx("income", "income", "100")
        resolved = MappingEnrichmentResolver(self.mappings).resolve(
            item,
            source(item, "同一个描述"),
        )
        self.assertIsNone(resolved.merchant_name)
        self.assertIsNone(resolved.default_category)
        self.assertEqual(resolved.category, INCOME_DEFAULT_CATEGORY)
        self.assertEqual(resolved.category_source, "income_default")
        self.assertFalse(resolved.is_unclassified)
        self.assertEqual(resolved.display_name, "同一个描述")

    def test_mapping_review_ignores_income_unmapped_descriptions(self) -> None:
        expense = tx("expense", "expense", "20")
        income = tx("income", "income", "100")
        sources = {
            expense.id: source(expense, "支出未映射"),
            income.id: source(income, "收入原始描述"),
        }
        states = {
            expense.id: TransactionEnrichmentState(
                transaction_id=expense.id,
                merchant_name=None,
                default_category=None,
                category="待分类",
                category_source="unclassified",
            ),
            income.id: TransactionEnrichmentState(
                transaction_id=income.id,
                merchant_name=None,
                default_category=None,
                category=INCOME_DEFAULT_CATEGORY,
                category_source="income_default",
            ),
        }
        items = build_mapping_review_items(
            (expense, income),
            MappingProxyType(sources),
            MappingProxyType(states),
            self.mappings,
        )
        self.assertEqual(tuple(item.description for item in items), ("支出未映射",))

    def test_income_default_round_trips_through_enrichment_store(self) -> None:
        item = tx("income", "income", "100")
        state = enrichment_state_from_result(
            MappingEnrichmentResolver(self.mappings).resolve(
                item,
                source(item, "工资"),
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "enrichment.jsonl"
            write_enrichment_states((state,), path)
            self.assertEqual(read_enrichment_states(path), (state,))


if __name__ == "__main__":
    unittest.main()
