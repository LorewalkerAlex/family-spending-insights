from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.domain.enrichment import (
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    EnrichmentDecision,
    consumption_review_signals,
    resolve_enrichment,
)
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.refund import reconcile_refunds
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import transaction_from_source_record


class RefundDomainTests(unittest.TestCase):
    def source(
        self,
        key: str,
        when: str,
        amount: str,
        description: str,
        *,
        transaction_type: str = "expense",
    ) -> SourceRecord:
        return SourceRecord(
            identity=SourceIdentity("manual", key, "record"),
            transaction_type=transaction_type,  # type: ignore[arg-type]
            transaction_date=date.fromisoformat(when),
            amount=Decimal(amount),
            currency="CNY",
            description=description,
        )

    def state(
        self,
        sources: tuple[SourceRecord, ...],
        mappings: MappingCatalog,
        decisions: dict[str, EnrichmentDecision] | None = None,
    ):
        transactions = tuple(transaction_from_source_record(source) for source in sources)
        by_transaction = {transaction.id: transaction for transaction in transactions}
        source_by_transaction = {
            transaction.id: source for transaction, source in zip(transactions, sources, strict=True)
        }
        decisions = decisions or {}
        enrichments = {
            transaction.id: resolve_enrichment(
                transaction,
                source_by_transaction[transaction.id],
                mappings,
                decisions.get(transaction.id),
            )
            for transaction in transactions
        }
        return transactions, by_transaction, source_by_transaction, enrichments

    def test_exact_description_and_partial_refund_preserve_purchase_identity(self) -> None:
        mappings = MappingCatalog(
            {"shop": "Shop"},
            {"Shop": "daily"},
            frozenset({"daily"}),
        )
        sources = (
            self.source("purchase", "2026-01-02", "300", "shop"),
            self.source("refund", "2026-01-10", "-100", "shop"),
        )
        transactions, _, source_by_transaction, enrichments = self.state(sources, mappings)
        result = reconcile_refunds(transactions, source_by_transaction, enrichments)

        self.assertEqual(len(result.net_consumption), 1)
        self.assertEqual(result.net_consumption[0].transaction_id, transactions[0].id)
        self.assertEqual(result.net_consumption[0].spending, Decimal("200"))
        self.assertEqual(result.partially_refunded_transactions, 1)
        self.assertEqual(result.refund_transactions, 1)

    def test_same_merchant_equal_amount_fallback_is_bounded_and_explainable(self) -> None:
        mappings = MappingCatalog(
            {"pay-shop": "Shop", "refund-shop": "Shop"},
            {"Shop": "daily"},
            frozenset({"daily"}),
        )
        sources = (
            self.source("purchase", "2026-01-02", "50", "pay-shop"),
            self.source("refund", "2026-01-20", "-50", "refund-shop"),
        )
        transactions, _, source_by_transaction, enrichments = self.state(sources, mappings)
        result = reconcile_refunds(transactions, source_by_transaction, enrichments)

        self.assertEqual(result.net_consumption, ())
        self.assertEqual(result.same_merchant_refund_matches, 1)
        self.assertEqual(result.same_merchant_matched_amount, Decimal("50"))
        self.assertEqual(result.fully_refunded_transactions, 1)

    def test_income_is_outside_refund_netting_and_zero_expense_is_counted(self) -> None:
        mappings = MappingCatalog.empty()
        sources = (
            self.source("income", "2026-01-02", "1000", "salary", transaction_type="income"),
            self.source("zero", "2026-01-03", "0", "zero"),
        )
        transactions, _, source_by_transaction, enrichments = self.state(sources, mappings)
        result = reconcile_refunds(transactions, source_by_transaction, enrichments)

        self.assertEqual(result.net_consumption, ())
        self.assertEqual(result.zero_amount_transactions, 1)
        self.assertEqual(result.refund_transactions, 0)

    def test_high_value_review_uses_net_spending_and_override_suppresses_review(self) -> None:
        # MappingCatalog requires every formal category to be used by a Merchant.
        mappings = MappingCatalog(
            {"shop": "Shop", "food": "Food"},
            {"Shop": "综合购物", "Food": "餐饮美食"},
            frozenset({"综合购物", "餐饮美食"}),
        )
        sources = (
            self.source("purchase", "2026-01-02", "1200", "shop"),
            self.source("refund", "2026-01-03", "-100", "shop"),
        )
        transactions, _, source_by_transaction, enrichments = self.state(sources, mappings)
        result = reconcile_refunds(transactions, source_by_transaction, enrichments)
        purchase_enrichment = enrichments[transactions[0].id]

        self.assertEqual(result.net_consumption[0].spending, Decimal("1100"))
        self.assertEqual(
            consumption_review_signals(purchase_enrichment, Decimal("1100")),
            (HIGH_VALUE_GENERAL_SHOPPING_REVIEW,),
        )

        override = EnrichmentDecision(
            transaction_id=transactions[0].id,
            category_override="餐饮美食",
        )
        overridden = resolve_enrichment(transactions[0], sources[0], mappings, override)
        self.assertEqual(consumption_review_signals(overridden, Decimal("1100")), ())


if __name__ == "__main__":
    unittest.main()
