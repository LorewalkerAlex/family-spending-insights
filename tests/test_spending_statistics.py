from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.mapping import ResolvedTransaction
from family_spending.spending_statistics import (
    SpendingStatisticsError,
    aggregate_spending,
)


def resolved(
    transaction_id: str,
    amount: str,
    transaction_date: date,
    *,
    category: str,
    merchant_name: str | None,
    display_name: str,
    is_unmatched: bool = False,
) -> ResolvedTransaction:
    transaction = CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        amount=Decimal(amount),
        description=display_name,
        source_email="statement.eml",
        source_index=1,
    )
    return ResolvedTransaction(
        transaction=transaction,
        merchant_name=merchant_name,
        display_name=display_name,
        default_category=None if is_unmatched else category,
        category=category,
        category_source="unclassified" if is_unmatched else "merchant_default",
        is_unmatched=is_unmatched,
        review_signals=(),
    )


class SpendingStatisticsTests(unittest.TestCase):
    def test_aggregates_month_category_and_merchant_with_reconciliation(self) -> None:
        statistics = aggregate_spending(
            (
                resolved(
                    "jan-coffee-1",
                    "-10.50",
                    date(2026, 1, 2),
                    category="餐饮美食",
                    merchant_name="咖啡店",
                    display_name="咖啡店",
                ),
                resolved(
                    "jan-coffee-2",
                    "-9.50",
                    date(2026, 1, 3),
                    category="餐饮美食",
                    merchant_name="咖啡店",
                    display_name="咖啡店",
                ),
                resolved(
                    "jan-unclassified",
                    "-30",
                    date(2026, 1, 4),
                    category="待分类",
                    merchant_name=None,
                    display_name="支付宝-未知商户",
                    is_unmatched=True,
                ),
                resolved(
                    "feb-appliance",
                    "-2000",
                    date(2026, 2, 1),
                    category="家居家电",
                    merchant_name="京东购物",
                    display_name="京东购物",
                ),
            )
        )

        self.assertEqual(statistics.total_spending, Decimal("2050.00"))
        self.assertEqual(statistics.transaction_count, 4)
        self.assertEqual(
            tuple(month.month for month in statistics.months),
            ("2026-02", "2026-01"),
        )

        january = statistics.months[1]
        self.assertEqual(january.total_spending, Decimal("50.00"))
        self.assertEqual(january.transaction_count, 3)
        self.assertEqual(
            tuple(
                (item.category, item.spending, item.transaction_count)
                for item in january.categories
            ),
            (
                ("待分类", Decimal("30"), 1),
                ("餐饮美食", Decimal("20.00"), 2),
            ),
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
            (
                (None, "支付宝-未知商户", True, Decimal("30"), 1),
                ("咖啡店", "咖啡店", False, Decimal("20.00"), 2),
            ),
        )
        self.assertEqual(
            sum(item.spending for item in january.categories),
            january.total_spending,
        )
        self.assertEqual(
            sum(item.spending for item in january.merchants),
            january.total_spending,
        )

    def test_empty_input_returns_empty_statistics(self) -> None:
        statistics = aggregate_spending(())
        self.assertEqual(statistics.total_spending, Decimal("0"))
        self.assertEqual(statistics.transaction_count, 0)
        self.assertEqual(statistics.months, ())

    def test_non_negative_input_is_rejected(self) -> None:
        item = resolved(
            "refund",
            "10",
            date(2026, 1, 1),
            category="餐饮美食",
            merchant_name="咖啡店",
            display_name="咖啡店",
        )
        with self.assertRaisesRegex(
            SpendingStatisticsError,
            "only negative net consumption",
        ):
            aggregate_spending((item,))


if __name__ == "__main__":
    unittest.main()
