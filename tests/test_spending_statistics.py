from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from family_spending.enrichment import TransactionEnrichment
from family_spending.refund_reconciliation import NetConsumption
from family_spending.spending_statistics import SpendingStatisticsError, aggregate_spending
from family_spending.transactions import Transaction


def transaction(transaction_id: str, amount: str, when: date) -> Transaction:
    """Keep core fixtures free of Merchant/description fields so analytics tests enforce the new model boundary."""
    return Transaction(
        id=transaction_id,
        transaction_type="expense",
        transaction_date=when,
        amount=Decimal(amount),
        currency="CNY",
    )


def enrichment(
    transaction_id: str,
    *,
    category: str,
    merchant_name: str | None,
    display_name: str,
    is_unclassified: bool = False,
) -> TransactionEnrichment:
    """Model current Enrichment separately because statistics must join it at read time rather than materialize it on Transaction."""
    return TransactionEnrichment(
        transaction_id=transaction_id,
        merchant_name=merchant_name,
        display_name=display_name,
        default_category=None if is_unclassified else category,
        category=category,
        category_source="unclassified" if is_unclassified else "merchant_default",
        is_unclassified=is_unclassified,
        review_signals=(),
    )


class SpendingStatisticsTests(unittest.TestCase):
    def test_aggregates_month_category_and_merchant_with_reconciliation(self) -> None:
        """Separate Transaction, Enrichment, and NetConsumption inputs must still reconcile to the same user-facing totals."""
        transactions = (
            transaction("jan-coffee-1", "10.50", date(2026, 1, 2)),
            transaction("jan-coffee-2", "9.50", date(2026, 1, 3)),
            transaction("jan-unclassified", "30", date(2026, 1, 4)),
            transaction("feb-appliance", "2000", date(2026, 2, 1)),
        )
        enrichments = (
            enrichment("jan-coffee-1", category="餐饮美食", merchant_name="咖啡店", display_name="咖啡店"),
            enrichment("jan-coffee-2", category="餐饮美食", merchant_name="咖啡店", display_name="咖啡店"),
            enrichment(
                "jan-unclassified",
                category="待分类",
                merchant_name=None,
                display_name="支付宝-未知商户",
                is_unclassified=True,
            ),
            enrichment("feb-appliance", category="家居家电", merchant_name="京东购物", display_name="京东购物"),
        )
        net = (
            NetConsumption("jan-coffee-1", Decimal("10.50")),
            NetConsumption("jan-coffee-2", Decimal("9.50")),
            NetConsumption("jan-unclassified", Decimal("30")),
            NetConsumption("feb-appliance", Decimal("2000")),
        )

        statistics = aggregate_spending(
            net,
            MappingProxyType({item.id: item for item in transactions}),
            MappingProxyType({item.transaction_id: item for item in enrichments}),
        )

        self.assertEqual(statistics.total_spending, Decimal("2050.00"))
        self.assertEqual(statistics.transaction_count, 4)
        self.assertEqual(tuple(month.month for month in statistics.months), ("2026-02", "2026-01"))
        january = statistics.months[1]
        self.assertEqual(january.total_spending, Decimal("50.00"))
        self.assertEqual(january.transaction_count, 3)
        self.assertEqual(
            tuple((item.category, item.spending, item.transaction_count) for item in january.categories),
            (("待分类", Decimal("30"), 1), ("餐饮美食", Decimal("20.00"), 2)),
        )
        self.assertEqual(
            tuple(
                (
                    item.merchant_name,
                    item.display_name,
                    item.is_unclassified,
                    item.spending,
                    item.transaction_count,
                )
                for item in january.merchants
            ),
            ((None, "支付宝-未知商户", True, Decimal("30"), 1), ("咖啡店", "咖啡店", False, Decimal("20.00"), 2)),
        )
        self.assertEqual(sum(item.spending for item in january.categories), january.total_spending)
        self.assertEqual(sum(item.spending for item in january.merchants), january.total_spending)

    def test_empty_input_returns_empty_statistics(self) -> None:
        """An empty derived view should remain a valid zero report rather than require placeholder Transactions or Enrichment."""
        statistics = aggregate_spending((), MappingProxyType({}), MappingProxyType({}))
        self.assertEqual(statistics.total_spending, Decimal("0"))
        self.assertEqual(statistics.transaction_count, 0)
        self.assertEqual(statistics.months, ())

    def test_non_positive_net_consumption_is_rejected(self) -> None:
        """NetConsumption uses positive spending by contract so old negative fake-Transaction semantics cannot leak back in."""
        item = transaction("bad", "10", date(2026, 1, 1))
        info = enrichment("bad", category="餐饮美食", merchant_name="咖啡店", display_name="咖啡店")
        with self.assertRaisesRegex(SpendingStatisticsError, "positive net consumption"):
            aggregate_spending(
                (NetConsumption("bad", Decimal("-10")),),
                MappingProxyType({"bad": item}),
                MappingProxyType({"bad": info}),
            )


if __name__ == "__main__":
    unittest.main()
