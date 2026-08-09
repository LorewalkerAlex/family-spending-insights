from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from family_spending.enrichment import TransactionEnrichment
from family_spending.refund_reconciliation import NetConsumption
from family_spending.spending_statistics import aggregate_spending
from family_spending.transactions import Transaction


class SpendingStatisticsMerchantIdentityTests(unittest.TestCase):
    def test_merchant_identity_is_independent_from_category_classification(self) -> None:
        transactions = (
            Transaction(
                id="known-merchant",
                transaction_type="expense",
                transaction_date=date(2026, 1, 5),
                amount=Decimal("20"),
                currency="CNY",
            ),
            Transaction(
                id="unknown-merchant",
                transaction_type="expense",
                transaction_date=date(2026, 1, 6),
                amount=Decimal("10"),
                currency="CNY",
            ),
        )
        enrichments = (
            TransactionEnrichment(
                transaction_id="known-merchant",
                merchant_name="手工商户",
                display_name="手工商户",
                default_category=None,
                category="待分类",
                category_source="unclassified",
                is_unclassified=True,
                review_signals=(),
            ),
            TransactionEnrichment(
                transaction_id="unknown-merchant",
                merchant_name=None,
                display_name="manual_unknown",
                default_category=None,
                category="餐饮美食",
                category_source="manual_override",
                is_unclassified=False,
                review_signals=(),
            ),
        )

        statistics = aggregate_spending(
            (
                NetConsumption("known-merchant", Decimal("20")),
                NetConsumption("unknown-merchant", Decimal("10")),
            ),
            MappingProxyType({item.id: item for item in transactions}),
            MappingProxyType({item.transaction_id: item for item in enrichments}),
        )

        self.assertEqual(
            tuple(
                (item.merchant_name, item.display_name, item.is_unclassified)
                for item in statistics.months[0].merchants
            ),
            (("手工商户", "手工商户", False), (None, "manual_unknown", True)),
        )
        self.assertEqual(
            tuple(item.category for item in statistics.months[0].categories),
            ("待分类", "餐饮美食"),
        )


if __name__ == "__main__":
    unittest.main()
