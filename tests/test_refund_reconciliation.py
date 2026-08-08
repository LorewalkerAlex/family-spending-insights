from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from family_spending.enrichment import TransactionEnrichment
from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.ingestion.cmb_source_adapter import CmbSourceAdapter
from family_spending.reconciliation import CmbReconciler
from family_spending.refund_reconciliation import reconcile_refunds
from family_spending.transactions import index_authoritative_source_records


def make_transaction(
    transaction_id: str,
    amount: str,
    *,
    transaction_date: date = date(2026, 1, 1),
    description: str = "支付宝-测试商户",
    source_index: int = 1,
) -> CmbTransaction:
    """Keep refund fixtures in raw CMB semantics so positive purchases and negative refunds stay authoritative facts."""
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        amount=Decimal(amount),
        description=description,
        source_email="statement.eml",
        source_index=source_index,
    )


def reconcile(
    raw_transactions: tuple[CmbTransaction, ...],
    merchant_by_description: dict[str, str] | None = None,
):
    """Build only the source/identity/enrichment context refund matching needs, avoiding unrelated Mapping rules in unit tests."""
    source_records = CmbSourceAdapter().adapt_all(raw_transactions)
    reconciliation = CmbReconciler().reconcile(source_records)
    source_by_transaction = index_authoritative_source_records(
        source_records,
        reconciliation.source_links,
    )
    merchants = merchant_by_description or {}
    enrichments = {}
    for transaction in reconciliation.transactions:
        source_record = source_by_transaction[transaction.id]
        merchant_name = merchants.get(source_record.description or "")
        enrichments[transaction.id] = TransactionEnrichment(
            transaction_id=transaction.id,
            merchant_name=merchant_name,
            display_name=merchant_name or source_record.description or source_record.id,
            default_category="测试分类" if merchant_name else None,
            category="测试分类" if merchant_name else "待分类",
            category_source="merchant_default" if merchant_name else "unclassified",
            is_unclassified=merchant_name is None,
            review_signals=(),
        )
    result = reconcile_refunds(
        reconciliation.transactions,
        source_by_transaction,
        MappingProxyType(enrichments),
    )
    source_id_by_transaction = {
        transaction_id: source_record.id
        for transaction_id, source_record in source_by_transaction.items()
    }
    return result, source_id_by_transaction, reconciliation.transactions


def net_rows(result, source_id_by_transaction):
    """Translate system IDs back to source IDs only for assertions so tests can compare legacy business behavior readably."""
    return tuple(
        (source_id_by_transaction[item.transaction_id], item.spending)
        for item in result.net_consumption
    )


class RefundReconciliationTests(unittest.TestCase):
    def test_no_refunds_preserves_purchase_order_and_positive_net_spending(self) -> None:
        """NetConsumption should preserve original purchase order even though matching processes transactions chronologically."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("later", "20", transaction_date=date(2026, 2, 1)),
                make_transaction("earlier", "10", transaction_date=date(2026, 1, 1)),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("later", Decimal("20")), ("earlier", Decimal("10"))))
        self.assertEqual(result.refund_transactions, 0)
        self.assertEqual(result.unmatched_refund_amount, Decimal("0"))

    def test_exact_refund_uses_most_recent_equal_balance(self) -> None:
        """Exact same-description balance stays first priority and must choose the newest eligible purchase."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("older", "100", source_index=1),
                make_transaction("newer", "100", source_index=2),
                make_transaction("refund", "-100", source_index=3),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("older", Decimal("100")),))
        self.assertEqual(result.fully_refunded_transactions, 1)

    def test_partial_refund_preserves_authoritative_transaction_and_derives_net_amount(self) -> None:
        """A partial refund must not rewrite the original Transaction amount now that net spending is a separate projection."""
        original = make_transaction("purchase", "3000", transaction_date=date(2025, 12, 10), source_index=4)
        result, source_ids, transactions = reconcile(
            (
                original,
                make_transaction("refund", "-1000", transaction_date=date(2026, 1, 10), source_index=1),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("purchase", Decimal("2000")),))
        self.assertEqual(tuple(item.amount for item in transactions), (Decimal("3000"), Decimal("-1000")))
        self.assertEqual(result.partially_refunded_transactions, 1)

    def test_refund_accumulates_from_most_recent_consumption(self) -> None:
        """Fallback accumulation remains newest-first so ambiguous same-description history resolves deterministically."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("older", "100", source_index=1),
                make_transaction("newer", "80", source_index=2),
                make_transaction("refund", "-120", source_index=3),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("older", Decimal("60")),))
        self.assertEqual(result.fully_refunded_transactions, 1)
        self.assertEqual(result.partially_refunded_transactions, 1)

    def test_multiple_refunds_reduce_same_consumption(self) -> None:
        """Repeated refunds should consume the same remaining balance rather than create independent negative spending rows."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("purchase", "100", source_index=1),
                make_transaction("refund-1", "-30", source_index=2),
                make_transaction("refund-2", "-20", source_index=3),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("purchase", Decimal("50")),))
        self.assertEqual(result.partially_refunded_transactions, 1)

    def test_same_day_uses_original_tuple_order(self) -> None:
        """Source order is the stable same-day tie-breaker because CMB records do not carry a stronger timestamp."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("purchase-1", "100", source_index=1),
                make_transaction("refund", "-100", source_index=2),
                make_transaction("purchase-2", "100", source_index=3),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("purchase-2", Decimal("100")),))

    def test_different_descriptions_never_offset_without_merchant_evidence(self) -> None:
        """Cross-description matching must remain impossible unless confirmed Merchant identity supplies the additional evidence."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("purchase", "100", description="商户A"),
                make_transaction("refund", "-100", description="商户B", source_index=2),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("purchase", Decimal("100")),))
        self.assertEqual(result.unmatched_refund_count, 1)
        self.assertEqual(result.unmatched_refund_amount, Decimal("100"))

    def test_refund_cannot_offset_future_consumption(self) -> None:
        """Chronology remains a hard boundary so a refund cannot be justified by a purchase that had not occurred yet."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("future-purchase", "100", transaction_date=date(2026, 2, 1)),
                make_transaction("earlier-refund", "-100", transaction_date=date(2026, 1, 1), source_index=2),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("future-purchase", Decimal("100")),))
        self.assertEqual(result.unmatched_refund_count, 1)

    def test_unmatched_remainder_is_excluded_and_summarized(self) -> None:
        """Only matched purchase balance becomes spending; unmatched refund remainder stays diagnostic rather than negative expense."""
        result, _, _ = reconcile(
            (
                make_transaction("purchase", "40", source_index=1),
                make_transaction("refund", "-100", source_index=2),
            )
        )
        self.assertEqual(result.net_consumption, ())
        self.assertEqual(result.unmatched_refund_count, 1)
        self.assertEqual(result.unmatched_refund_amount, Decimal("60"))

    def test_zero_amount_is_ignored_and_summarized(self) -> None:
        """Zero source facts remain observable in diagnostics but must never become consumption rows."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("zero", "0", source_index=1),
                make_transaction("purchase", "20", source_index=2),
            )
        )
        self.assertEqual(net_rows(result, source_ids), (("purchase", Decimal("20")),))
        self.assertEqual(result.zero_amount_transactions, 1)
        self.assertEqual(result.refund_transactions, 0)

    def test_same_merchant_equal_amount_within_30_days_is_matched(self) -> None:
        """Confirmed Merchant identity remains a bounded alias bridge when descriptions differ but amount/date evidence is strong."""
        result, _, _ = reconcile(
            (
                make_transaction("purchase", "100", transaction_date=date(2026, 1, 1), description="支付宝-商户消费"),
                make_transaction("refund", "-100", transaction_date=date(2026, 1, 20), description="退款-支付宝-商户消费", source_index=2),
            ),
            {"支付宝-商户消费": "测试商户", "退款-支付宝-商户消费": "测试商户"},
        )
        self.assertEqual(result.net_consumption, ())
        self.assertEqual(result.same_merchant_refund_matches, 1)
        self.assertEqual(result.same_merchant_matched_amount, Decimal("100"))

    def test_exact_description_has_priority_over_newer_merchant_alias(self) -> None:
        """Exact source-description evidence must outrank Merchant alias evidence even when the alias purchase is newer."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("exact-description-purchase", "100", transaction_date=date(2026, 1, 1), description="退款描述"),
                make_transaction("merchant-alias-purchase", "100", transaction_date=date(2026, 1, 10), description="消费描述", source_index=2),
                make_transaction("refund", "-100", transaction_date=date(2026, 1, 20), description="退款描述", source_index=3),
            ),
            {"退款描述": "测试商户", "消费描述": "测试商户"},
        )
        self.assertEqual(net_rows(result, source_ids), (("merchant-alias-purchase", Decimal("100")),))
        self.assertEqual(result.same_merchant_refund_matches, 0)

    def test_same_merchant_equal_amount_precedes_description_accumulation(self) -> None:
        """An exact amount plus confirmed Merchant is stronger evidence than combining several same-description balances."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("small-same-description", "22.19", transaction_date=date(2026, 5, 1), description="京东退款描述"),
                make_transaction("equal-merchant-alias", "538.00", transaction_date=date(2026, 5, 2), description="京东消费描述", source_index=2),
                make_transaction("refund", "-538.00", transaction_date=date(2026, 5, 3), description="京东退款描述", source_index=3),
            ),
            {"京东退款描述": "京东购物", "京东消费描述": "京东购物"},
        )
        self.assertEqual(net_rows(result, source_ids), (("small-same-description", Decimal("22.19")),))
        self.assertEqual(result.same_merchant_refund_matches, 1)
        self.assertEqual(result.same_merchant_matched_amount, Decimal("538.00"))
        self.assertEqual(result.unmatched_refund_amount, Decimal("0"))

    def test_same_merchant_match_uses_most_recent_candidate(self) -> None:
        """When merchant evidence ties, the most recent eligible purchase remains the deterministic candidate."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("older", "100", transaction_date=date(2026, 1, 5), description="商户别名A"),
                make_transaction("newer", "100", transaction_date=date(2026, 1, 10), description="商户别名B", source_index=2),
                make_transaction("refund", "-100", transaction_date=date(2026, 1, 20), description="退款别名", source_index=3),
            ),
            {"商户别名A": "测试商户", "商户别名B": "测试商户", "退款别名": "测试商户"},
        )
        self.assertEqual(net_rows(result, source_ids), (("older", Decimal("100")),))
        self.assertEqual(result.same_merchant_refund_matches, 1)

    def test_same_merchant_equal_amount_outside_30_days_is_not_matched(self) -> None:
        """Merchant alias evidence expires after 30 natural days so weak long-range matches cannot silently alter spending."""
        result, source_ids, _ = reconcile(
            (
                make_transaction("purchase", "100", transaction_date=date(2026, 1, 1), description="消费别名"),
                make_transaction("refund", "-100", transaction_date=date(2026, 2, 1), description="退款别名", source_index=2),
            ),
            {"消费别名": "测试商户", "退款别名": "测试商户"},
        )
        self.assertEqual(net_rows(result, source_ids), (("purchase", Decimal("100")),))
        self.assertEqual(result.same_merchant_refund_matches, 0)
        self.assertEqual(result.unmatched_refund_amount, Decimal("100"))


if __name__ == "__main__":
    unittest.main()
