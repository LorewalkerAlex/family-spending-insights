from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from family_spending.enrichment import (
    HIGH_VALUE_GENERAL_SHOPPING_REVIEW,
    OTHER_EXPENSE_REVIEW,
    consumption_review_signals,
)
from family_spending.ingestion.cmb_email_transactions import CmbTransaction, write_transactions_csv
from family_spending.ingestion.cmb_source_adapter import CmbSourceAdapter
from family_spending.mapping import (
    MappingEnrichmentResolver,
    MerchantMappings,
    bind_transaction_category_overrides,
)
from family_spending.reconciliation import CmbReconciler
from family_spending.refund_reconciliation import reconcile_refunds
from family_spending.spending_statistics import aggregate_spending
from family_spending.transaction_resolution import build_cmb_domain_state, resolve_transactions
from family_spending.transactions import TransactionSourceLink, build_transaction_id


def _cmb(
    transaction_id: str,
    when: str,
    amount: str,
    description: str,
    *,
    source_index: int,
) -> CmbTransaction:
    """Keep fixtures explicit about source identity because this migration separates it from system identity."""
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=date.fromisoformat(when),
        amount=Decimal(amount),
        description=description,
        source_email="2026-01-10_bill.eml",
        source_index=source_index,
    )


def _mappings(
    *,
    descriptions: dict[str, str],
    categories: dict[str, str],
    overrides: dict[str, str] | None = None,
) -> MerchantMappings:
    """Construct reviewed Mapping state directly so architecture tests isolate behavior from YAML parsing."""
    return MerchantMappings(
        description_to_merchant=MappingProxyType(descriptions),
        merchant_to_category=MappingProxyType(categories),
        transaction_category_overrides=MappingProxyType(overrides or {}),
        categories=frozenset(categories.values()),
        merchants_path=Path("merchants.yaml"),
        categories_path=Path("categories.yaml"),
        overrides_path=Path("transaction_category_overrides.jsonl"),
    )


class SourceAndReconciliationTests(unittest.TestCase):
    def test_cmb_adapter_preserves_source_fields_without_reusing_source_id_as_transaction_id(self) -> None:
        """The first migration boundary must be lossless while making source and system identity visibly different."""
        raw = _cmb("cmb_source_1", "2026-01-02", "88.50", "支付宝-商户", source_index=7)

        record = CmbSourceAdapter().adapt(raw)
        result = CmbReconciler().reconcile((record,))
        transaction = result.transactions[0]

        self.assertEqual(record.id, "cmb_source_1")
        self.assertEqual(record.description, "支付宝-商户")
        self.assertEqual(record.provenance.source_email, raw.source_email)
        self.assertEqual(record.provenance.source_index, 7)
        self.assertEqual(transaction.id, build_transaction_id(record))
        self.assertTrue(transaction.id.startswith("txn_"))
        self.assertNotEqual(transaction.id, record.id)
        self.assertEqual(transaction.amount, Decimal("88.50"))
        self.assertFalse(hasattr(transaction, "description"))
        self.assertEqual(result.source_links[0].role, "authoritative")

    def test_cmb_reconciliation_reuses_identity_and_refreshes_authoritative_facts(self) -> None:
        """Reruns must be idempotent, while a changed authoritative CMB fact must refresh the same Transaction identity."""
        adapter = CmbSourceAdapter()
        original = adapter.adapt(_cmb("cmb_source_1", "2026-01-02", "88.50", "A", source_index=1))
        first = CmbReconciler().reconcile((original,))
        updated = adapter.adapt(_cmb("cmb_source_1", "2026-01-03", "90.00", "A", source_index=1))

        second = CmbReconciler().reconcile(
            (updated,),
            existing_transactions=first.transactions,
            existing_links=first.source_links,
        )

        self.assertEqual(second.transactions[0].id, first.transactions[0].id)
        self.assertEqual(second.transactions[0].transaction_date, date(2026, 1, 3))
        self.assertEqual(second.transactions[0].amount, Decimal("90.00"))
        self.assertEqual(second.decisions[0].action, "reused")
        self.assertTrue(second.decisions[0].evidence.source_identity_match)
        self.assertEqual(second.source_links[0].role, "authoritative")

    def test_cmb_reconciliation_upgrades_existing_supporting_link_to_authoritative(self) -> None:
        """When CMB arrives for a previously supporting link, its authority must be explicit rather than inherited accidentally."""
        record = CmbSourceAdapter().adapt(_cmb("cmb_source_1", "2026-01-02", "88.50", "A", source_index=1))
        transaction_id = build_transaction_id(record)
        initial = CmbReconciler().reconcile((record,))
        supporting = TransactionSourceLink(
            transaction_id=transaction_id,
            source_record_id=record.id,
            role="supporting",
        )

        rerun = CmbReconciler().reconcile(
            (record,),
            existing_transactions=initial.transactions,
            existing_links=(supporting,),
        )

        self.assertEqual(rerun.source_links, (TransactionSourceLink(transaction_id, record.id, "authoritative"),))


class EnrichmentAndOverrideTests(unittest.TestCase):
    def test_legacy_override_is_bound_from_cmb_source_id_to_system_transaction_id(self) -> None:
        """Reviewed JSONL IDs must survive migration even though they historically referred to CMB source identity."""
        raw = (_cmb("cmb_override", "2026-01-02", "3660.81", "京东支付-电视", source_index=1),)
        mappings = _mappings(
            descriptions={"京东支付-电视": "京东购物"},
            categories={"京东购物": "综合购物"},
            overrides={"cmb_override": "家居家电"},
        )

        state = build_cmb_domain_state(raw, mappings)
        transaction = state.reconciliation.transactions[0]
        enrichment = state.enrichments[0]

        self.assertNotEqual(transaction.id, "cmb_override")
        self.assertEqual(enrichment.transaction_id, transaction.id)
        self.assertEqual(enrichment.default_category, "综合购物")
        self.assertEqual(enrichment.category, "家居家电")
        self.assertEqual(enrichment.category_source, "transaction_override")

    def test_high_value_review_uses_positive_net_spending_after_refund_netting(self) -> None:
        """The threshold belongs to net consumption, not to the old negative fake-Transaction amount convention."""
        raw = (
            _cmb("cmb_purchase", "2026-01-02", "1200", "京东", source_index=1),
            _cmb("cmb_refund", "2026-01-03", "-100", "京东", source_index=2),
        )
        mappings = _mappings(
            descriptions={"京东": "京东购物"},
            categories={"京东购物": "综合购物"},
        )

        batch = resolve_transactions(raw, mappings)

        self.assertEqual(batch.net_consumption[0].spending, Decimal("1100"))
        reviews = batch.reviews_by_signal[HIGH_VALUE_GENERAL_SHOPPING_REVIEW]
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].source_record.id, "cmb_purchase")

    def test_other_expense_review_remains_enrichment_based(self) -> None:
        """Category-only review signals should remain stable even though high-value review moves after refund netting."""
        raw = (_cmb("cmb_other", "2026-01-02", "10", "丰巢", source_index=1),)
        mappings = _mappings(
            descriptions={"丰巢": "丰巢"},
            categories={"丰巢": "其他支出"},
        )

        batch = resolve_transactions(raw, mappings)

        self.assertEqual(len(batch.reviews_by_signal[OTHER_EXPENSE_REVIEW]), 1)


class RefundAndStatisticsTests(unittest.TestCase):
    def test_refund_netting_preserves_core_transactions_and_returns_positive_net_consumption(self) -> None:
        """Refund analysis must derive net spending without mutating the authoritative positive purchase and negative refund facts."""
        raw = (
            _cmb("cmb_purchase", "2026-01-02", "3000", "商户A", source_index=1),
            _cmb("cmb_refund", "2026-01-10", "-1000", "商户A", source_index=2),
        )
        mappings = _mappings(
            descriptions={"商户A": "商户A"},
            categories={"商户A": "餐饮美食"},
        )
        state = build_cmb_domain_state(raw, mappings)

        result = reconcile_refunds(
            state.reconciliation.transactions,
            state.source_records_by_transaction_id,
            state.enrichments_by_transaction_id,
        )

        amounts = tuple(item.amount for item in state.reconciliation.transactions)
        self.assertEqual(amounts, (Decimal("3000"), Decimal("-1000")))
        self.assertEqual(result.net_consumption[0].spending, Decimal("2000"))
        self.assertEqual(result.partially_refunded_transactions, 1)
        self.assertEqual(result.fully_refunded_transactions, 0)

    def test_same_merchant_fallback_can_match_different_descriptions_within_30_days(self) -> None:
        """Merchant identity stays a bounded reconciliation signal so current reviewed behavior survives the model split."""
        raw = (
            _cmb("cmb_purchase", "2026-01-02", "50", "财付通-瑞幸咖啡", source_index=1),
            _cmb("cmb_refund", "2026-01-20", "-50", "支付宝-瑞幸咖啡", source_index=2),
        )
        mappings = _mappings(
            descriptions={
                "财付通-瑞幸咖啡": "瑞幸咖啡",
                "支付宝-瑞幸咖啡": "瑞幸咖啡",
            },
            categories={"瑞幸咖啡": "餐饮美食"},
        )
        state = build_cmb_domain_state(raw, mappings)

        result = reconcile_refunds(
            state.reconciliation.transactions,
            state.source_records_by_transaction_id,
            state.enrichments_by_transaction_id,
        )

        self.assertEqual(result.net_consumption, ())
        self.assertEqual(result.same_merchant_refund_matches, 1)
        self.assertEqual(result.same_merchant_matched_amount, Decimal("50"))
        self.assertEqual(result.fully_refunded_transactions, 1)

    def test_statistics_join_transaction_and_current_enrichment_without_materialized_enriched_transaction(self) -> None:
        """Analytics must reconcile amounts, counts, category, and display name from separate current domain states."""
        raw = (
            _cmb("cmb_a", "2026-01-02", "20", "餐厅", source_index=1),
            _cmb("cmb_b", "2026-01-03", "30", "未知", source_index=2),
        )
        mappings = _mappings(
            descriptions={"餐厅": "餐厅"},
            categories={"餐厅": "餐饮美食"},
        )
        state = build_cmb_domain_state(raw, mappings)
        refunds = reconcile_refunds(
            state.reconciliation.transactions,
            state.source_records_by_transaction_id,
            state.enrichments_by_transaction_id,
        )

        statistics = aggregate_spending(
            refunds.net_consumption,
            state.transactions_by_id,
            state.enrichments_by_transaction_id,
        )

        self.assertEqual(statistics.total_spending, Decimal("50"))
        self.assertEqual(statistics.transaction_count, 2)
        self.assertEqual(statistics.months[0].month, "2026-01")
        self.assertEqual(sum(item.spending for item in statistics.months[0].categories), Decimal("50"))
        self.assertEqual(sum(item.spending for item in statistics.months[0].merchants), Decimal("50"))
        unknown = next(item for item in statistics.months[0].merchants if item.is_unclassified)
        self.assertEqual(unknown.display_name, "未知")


if __name__ == "__main__":
    unittest.main()
