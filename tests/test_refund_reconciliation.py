from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.refund_reconciliation import reconcile_refunds


def make_transaction(
    transaction_id: str,
    amount: str,
    *,
    transaction_date: date = date(2026, 1, 1),
    description: str = "支付宝-测试商户",
    source_index: int = 1,
) -> CmbTransaction:
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        amount=Decimal(amount),
        description=description,
        source_email="statement.eml",
        source_index=source_index,
    )


class RefundReconciliationTests(unittest.TestCase):
    def test_no_refunds_preserves_transactions_and_order(self) -> None:
        transactions = (
            make_transaction("later", "20", transaction_date=date(2026, 2, 1)),
            make_transaction("earlier", "10", transaction_date=date(2026, 1, 1)),
        )
        result = reconcile_refunds(transactions)
        self.assertEqual(
            tuple((item.transaction_id, item.amount) for item in result.net_transactions),
            (("later", Decimal("-20")), ("earlier", Decimal("-10"))),
        )
        self.assertEqual(result.refund_transactions, 0)
        self.assertEqual(result.unmatched_refund_amount, Decimal("0"))

    def test_exact_refund_uses_most_recent_equal_balance(self) -> None:
        transactions = (
            make_transaction("older", "100", source_index=1),
            make_transaction("newer", "100", source_index=2),
            make_transaction("refund", "-100", source_index=3),
        )
        result = reconcile_refunds(transactions)
        self.assertEqual(
            tuple(item.transaction_id for item in result.net_transactions),
            ("older",),
        )
        self.assertEqual(result.fully_refunded_transactions, 1)

    def test_partial_refund_preserves_original_identity(self) -> None:
        original = make_transaction(
            "purchase",
            "3000",
            transaction_date=date(2025, 12, 10),
            source_index=4,
        )
        result = reconcile_refunds(
            (
                original,
                make_transaction(
                    "refund",
                    "-1000",
                    transaction_date=date(2026, 1, 10),
                    source_index=1,
                ),
            )
        )
        self.assertEqual(len(result.net_transactions), 1)
        net = result.net_transactions[0]
        self.assertEqual(net.transaction_id, original.transaction_id)
        self.assertEqual(net.transaction_date, original.transaction_date)
        self.assertEqual(net.description, original.description)
        self.assertEqual(net.source_email, original.source_email)
        self.assertEqual(net.source_index, original.source_index)
        self.assertEqual(net.amount, Decimal("-2000"))
        self.assertEqual(original.amount, Decimal("3000"))
        self.assertEqual(result.partially_refunded_transactions, 1)

    def test_refund_accumulates_from_most_recent_consumption(self) -> None:
        transactions = (
            make_transaction("older", "100", source_index=1),
            make_transaction("newer", "80", source_index=2),
            make_transaction("refund", "-120", source_index=3),
        )
        result = reconcile_refunds(transactions)
        self.assertEqual(
            tuple((item.transaction_id, item.amount) for item in result.net_transactions),
            (("older", Decimal("-60")),),
        )
        self.assertEqual(result.fully_refunded_transactions, 1)
        self.assertEqual(result.partially_refunded_transactions, 1)

    def test_multiple_refunds_reduce_same_consumption(self) -> None:
        transactions = (
            make_transaction("purchase", "100", source_index=1),
            make_transaction("refund-1", "-30", source_index=2),
            make_transaction("refund-2", "-20", source_index=3),
        )
        result = reconcile_refunds(transactions)
        self.assertEqual(result.net_transactions[0].amount, Decimal("-50"))
        self.assertEqual(result.partially_refunded_transactions, 1)

    def test_same_day_uses_original_tuple_order(self) -> None:
        transactions = (
            make_transaction("purchase-1", "100", source_index=1),
            make_transaction("refund", "-100", source_index=2),
            make_transaction("purchase-2", "100", source_index=3),
        )
        result = reconcile_refunds(transactions)
        self.assertEqual(
            tuple(item.transaction_id for item in result.net_transactions),
            ("purchase-2",),
        )

    def test_different_descriptions_never_offset(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction("purchase", "100", description="商户A"),
                make_transaction("refund", "-100", description="商户B", source_index=2),
            )
        )
        self.assertEqual(len(result.net_transactions), 1)
        self.assertEqual(result.unmatched_refund_count, 1)
        self.assertEqual(result.unmatched_refund_amount, Decimal("100"))

    def test_refund_cannot_offset_future_consumption(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction(
                    "future-purchase",
                    "100",
                    transaction_date=date(2026, 2, 1),
                ),
                make_transaction(
                    "earlier-refund",
                    "-100",
                    transaction_date=date(2026, 1, 1),
                    source_index=2,
                ),
            )
        )
        self.assertEqual(
            tuple(item.transaction_id for item in result.net_transactions),
            ("future-purchase",),
        )
        self.assertEqual(result.unmatched_refund_count, 1)

    def test_unmatched_remainder_is_excluded_and_summarized(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction("purchase", "40", source_index=1),
                make_transaction("refund", "-100", source_index=2),
            )
        )
        self.assertEqual(result.net_transactions, ())
        self.assertEqual(result.unmatched_refund_count, 1)
        self.assertEqual(result.unmatched_refund_amount, Decimal("60"))

    def test_zero_amount_is_ignored_and_summarized(self) -> None:
        purchase = make_transaction("purchase", "20", source_index=2)
        result = reconcile_refunds(
            (
                make_transaction("zero", "0", source_index=1),
                purchase,
            )
        )
        self.assertEqual(len(result.net_transactions), 1)
        self.assertEqual(result.net_transactions[0].transaction_id, purchase.transaction_id)
        self.assertEqual(result.net_transactions[0].amount, Decimal("-20"))
        self.assertEqual(result.zero_amount_transactions, 1)
        self.assertEqual(result.refund_transactions, 0)
        self.assertEqual(result.unmatched_refund_amount, Decimal("0"))

    def test_same_merchant_equal_amount_within_30_days_is_matched(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction(
                    "purchase",
                    "100",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-商户消费",
                ),
                make_transaction(
                    "refund",
                    "-100",
                    transaction_date=date(2026, 1, 20),
                    description="退款-支付宝-商户消费",
                    source_index=2,
                ),
            ),
            {
                "支付宝-商户消费": "测试商户",
                "退款-支付宝-商户消费": "测试商户",
            },
        )
        self.assertEqual(result.net_transactions, ())
        self.assertEqual(result.same_merchant_refund_matches, 1)
        self.assertEqual(result.same_merchant_matched_amount, Decimal("100"))
        self.assertEqual(result.unmatched_refund_count, 0)

    def test_exact_description_has_priority_over_newer_merchant_alias(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction(
                    "exact-description-purchase",
                    "100",
                    transaction_date=date(2026, 1, 1),
                    description="退款描述",
                ),
                make_transaction(
                    "merchant-alias-purchase",
                    "100",
                    transaction_date=date(2026, 1, 10),
                    description="消费描述",
                    source_index=2,
                ),
                make_transaction(
                    "refund",
                    "-100",
                    transaction_date=date(2026, 1, 20),
                    description="退款描述",
                    source_index=3,
                ),
            ),
            {
                "退款描述": "测试商户",
                "消费描述": "测试商户",
            },
        )
        self.assertEqual(
            tuple(item.transaction_id for item in result.net_transactions),
            ("merchant-alias-purchase",),
        )
        self.assertEqual(result.same_merchant_refund_matches, 0)

    def test_same_merchant_equal_amount_precedes_description_accumulation(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction(
                    "small-same-description",
                    "22.19",
                    transaction_date=date(2026, 5, 1),
                    description="京东退款描述",
                ),
                make_transaction(
                    "equal-merchant-alias",
                    "538.00",
                    transaction_date=date(2026, 5, 2),
                    description="京东消费描述",
                    source_index=2,
                ),
                make_transaction(
                    "refund",
                    "-538.00",
                    transaction_date=date(2026, 5, 3),
                    description="京东退款描述",
                    source_index=3,
                ),
            ),
            {
                "京东退款描述": "京东购物",
                "京东消费描述": "京东购物",
            },
        )
        self.assertEqual(
            tuple(
                (item.transaction_id, item.amount)
                for item in result.net_transactions
            ),
            (("small-same-description", Decimal("-22.19")),),
        )
        self.assertEqual(result.same_merchant_refund_matches, 1)
        self.assertEqual(result.same_merchant_matched_amount, Decimal("538.00"))
        self.assertEqual(result.unmatched_refund_amount, Decimal("0"))

    def test_same_merchant_match_uses_most_recent_candidate(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction(
                    "older",
                    "100",
                    transaction_date=date(2026, 1, 5),
                    description="商户别名A",
                ),
                make_transaction(
                    "newer",
                    "100",
                    transaction_date=date(2026, 1, 10),
                    description="商户别名B",
                    source_index=2,
                ),
                make_transaction(
                    "refund",
                    "-100",
                    transaction_date=date(2026, 1, 20),
                    description="退款别名",
                    source_index=3,
                ),
            ),
            {
                "商户别名A": "测试商户",
                "商户别名B": "测试商户",
                "退款别名": "测试商户",
            },
        )
        self.assertEqual(
            tuple(item.transaction_id for item in result.net_transactions),
            ("older",),
        )
        self.assertEqual(result.same_merchant_refund_matches, 1)

    def test_same_merchant_equal_amount_outside_30_days_is_not_matched(self) -> None:
        result = reconcile_refunds(
            (
                make_transaction(
                    "purchase",
                    "100",
                    transaction_date=date(2026, 1, 1),
                    description="消费别名",
                ),
                make_transaction(
                    "refund",
                    "-100",
                    transaction_date=date(2026, 2, 1),
                    description="退款别名",
                    source_index=2,
                ),
            ),
            {
                "消费别名": "测试商户",
                "退款别名": "测试商户",
            },
        )
        self.assertEqual(
            tuple(item.transaction_id for item in result.net_transactions),
            ("purchase",),
        )
        self.assertEqual(result.same_merchant_refund_matches, 0)
        self.assertEqual(result.unmatched_refund_amount, Decimal("100"))


if __name__ == "__main__":
    unittest.main()
