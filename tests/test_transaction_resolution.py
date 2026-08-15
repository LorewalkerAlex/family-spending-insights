from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from family_spending.enrichment import (
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    OTHER_EXPENSE_REVIEW,
)
from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.mapping import MerchantMappings
from family_spending.transaction_resolution import resolve_transactions


def make_transaction(
    transaction_id: str,
    description: str,
    *,
    amount: str = "10.00",
    source_index: int = 1,
) -> CmbTransaction:
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=date(2026, 8, 1),
        amount=Decimal(amount),
        description=description,
        source_email="statement.eml",
        source_index=source_index,
    )


def mappings() -> MerchantMappings:
    descriptions = {
        "支付宝-测试餐饮": "测试餐饮",
        "支付宝-测试其他": "测试其他",
        "支付宝-测试购物": "测试购物",
        "支付宝-测试家电": "测试家电",
    }
    categories = {
        "测试餐饮": "餐饮美食",
        "测试其他": "其他支出",
        "测试购物": "综合购物",
        "测试家电": "家居家电",
    }
    return MerchantMappings(
        description_to_merchant=MappingProxyType(descriptions),
        merchant_to_category=MappingProxyType(categories),
        categories=frozenset(categories.values()),
        merchants_path=Path("merchants.yaml"),
        categories_path=Path("categories.yaml"),
    )


class TransactionResolutionTests(unittest.TestCase):
    def test_pure_batch_preserves_order_and_actionable_review_groups(self) -> None:
        transactions = (
            make_transaction("cmb_default", "支付宝-测试餐饮", source_index=1),
            make_transaction("cmb_appliance", "支付宝-测试家电", source_index=2),
            make_transaction("cmb_unclassified", "支付宝-未知商户", source_index=3),
            make_transaction("cmb_other", "支付宝-测试其他", source_index=4),
            make_transaction(
                "cmb_high_value",
                "支付宝-测试购物",
                amount="1000",
                source_index=5,
            ),
        )

        batch = resolve_transactions(transactions, mappings())

        self.assertEqual(
            tuple(item.source_record.id for item in batch.transactions),
            tuple(item.transaction_id for item in transactions),
        )
        self.assertTrue(
            all(item.transaction.id.startswith("txn_") for item in batch.transactions)
        )
        self.assertEqual(batch.category_source_counts["merchant_default"], 4)
        self.assertEqual(batch.category_source_counts["transaction_override"], 0)
        self.assertEqual(batch.category_source_counts["unclassified"], 1)
        self.assertEqual(
            tuple(item.source_record.id for item in batch.unclassified),
            ("cmb_unclassified",),
        )
        self.assertEqual(
            tuple(
                item.source_record.id
                for item in batch.reviews_by_signal[OTHER_EXPENSE_REVIEW]
            ),
            ("cmb_other",),
        )
        self.assertEqual(
            tuple(
                item.source_record.id
                for item in batch.reviews_by_signal[
                    HIGH_VALUE_GENERAL_SHOPPING_REVIEW
                ]
            ),
            ("cmb_high_value",),
        )


if __name__ == "__main__":
    unittest.main()
