from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from family_spending.enrichment import UNCLASSIFIED_CATEGORY
from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.manual_input import submit_manual_input
from family_spending.manual_source import (
    ManualSourceAdapter,
    create_manual_source_entry,
    read_manual_source_entries,
    write_manual_source_entries,
)
from family_spending.mapping import MerchantMappings
from family_spending.reconciliation import (
    CmbReconciler,
    ManualReconciler,
    ReconciliationContext,
    ReconciliationError,
)
from family_spending.refund_reconciliation import reconcile_refunds
from family_spending.source_link_store import (
    read_transaction_source_links,
    write_transaction_source_links,
)
from family_spending.transaction_resolution import build_household_domain_state
from family_spending.transactions import index_authoritative_source_records


def mappings() -> MerchantMappings:
    """Build current Mapping rules directly without any transaction-specific fact source."""
    return MerchantMappings(
        description_to_merchant=MappingProxyType(
            {
                "支付宝-咖啡": "咖啡店",
                "支付宝-家电": "京东购物",
            }
        ),
        merchant_to_category=MappingProxyType(
            {
                "咖啡店": "餐饮美食",
                "京东购物": "综合购物",
            }
        ),
        categories=frozenset({"餐饮美食", "综合购物", "家居家电"}),
        merchants_path=Path("merchants.yaml"),
        categories_path=Path("categories.yaml"),
    )


def cmb(source_id: str, when: str, amount: str, description: str) -> CmbTransaction:
    """Create one CMB fixture with a stable source ID."""
    return CmbTransaction(source_id, date.fromisoformat(when), Decimal(amount), description, "bill.eml", 1)


def manual(
    source_id: str,
    when: str,
    amount: str,
    *,
    merchant=None,
    category=None,
    note=None,
    transaction_type="expense",
):
    """Create one Manual Source fixture while keeping optional legacy source fields explicit."""
    return create_manual_source_entry(
        source_record_id=source_id,
        transaction_type=transaction_type,
        transaction_date=date.fromisoformat(when),
        amount=Decimal(amount),
        merchant_name=merchant,
        category=category,
        note=note,
    )


class ManualSourceStorageTests(unittest.TestCase):
    def test_manual_jsonl_round_trip_preserves_decimal_and_optional_enrichment(self) -> None:
        entry = manual("manual_1", "2026-08-01", "88.50", merchant="咖啡店", category="餐饮美食", note="早餐")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manual.jsonl"
            write_manual_source_entries((entry,), path)
            loaded = read_manual_source_entries(path)
        self.assertEqual(loaded, (entry,))
        record = ManualSourceAdapter().adapt(entry)
        self.assertIsNone(record.description)
        self.assertFalse(hasattr(record, "merchant_name"))

    def test_source_link_store_round_trip(self) -> None:
        state = build_household_domain_state((), (manual("manual_1", "2026-08-01", "10"),), mappings())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "links.jsonl"
            write_transaction_source_links(state.reconciliation.source_links, path)
            loaded = read_transaction_source_links(path)
        self.assertEqual(loaded, state.reconciliation.source_links)


class CrossSourceReconciliationTests(unittest.TestCase):
    def test_manual_only_creates_transaction_and_keeps_user_enrichment(self) -> None:
        entry = manual("manual_1", "2026-08-01", "88.50", merchant="咖啡店", category="餐饮美食", note="现金")
        state = build_household_domain_state((), (entry,), mappings())
        self.assertEqual(len(state.reconciliation.transactions), 1)
        transaction = state.reconciliation.transactions[0]
        link = state.reconciliation.source_links[0]
        enrichment = state.enrichments_by_transaction_id[transaction.id]
        self.assertEqual(link.role, "authoritative")
        self.assertEqual(enrichment.merchant_name, "咖啡店")
        self.assertEqual(enrichment.category, "餐饮美食")
        self.assertEqual(enrichment.category_source, "manual_override")
        self.assertEqual(enrichment.note, "现金")

    def test_manual_matches_existing_cmb_transaction_without_duplicate(self) -> None:
        card = cmb("cmb_1", "2026-08-02", "88.50", "支付宝-咖啡")
        entry = manual("manual_1", "2026-08-01", "88.50", merchant="咖啡店", note="补充")
        state = build_household_domain_state((card,), (entry,), mappings())
        self.assertEqual(len(state.reconciliation.transactions), 1)
        links = state.reconciliation.source_links
        self.assertEqual({link.source_record_id for link in links}, {"cmb_1", "manual_1"})
        self.assertEqual(next(link for link in links if link.source_record_id == "cmb_1").role, "authoritative")
        self.assertEqual(next(link for link in links if link.source_record_id == "manual_1").role, "supporting")
        tx = state.reconciliation.transactions[0]
        self.assertEqual(state.enrichments_by_transaction_id[tx.id].note, "补充")

    def test_cmb_arriving_after_manual_reuses_manual_transaction_id_and_takes_authority(self) -> None:
        entry = manual("manual_1", "2026-08-01", "88.50", merchant="咖啡店", category="餐饮美食", note="先录")
        first = build_household_domain_state((), (entry,), mappings())
        manual_tx_id = first.reconciliation.transactions[0].id

        card = cmb("cmb_1", "2026-08-02", "88.50", "支付宝-咖啡")
        second = build_household_domain_state(
            (card,),
            (entry,),
            mappings(),
            existing_links=first.reconciliation.source_links,
        )
        self.assertEqual(len(second.reconciliation.transactions), 1)
        tx = second.reconciliation.transactions[0]
        self.assertEqual(tx.id, manual_tx_id)
        self.assertEqual(tx.transaction_date, date(2026, 8, 2))
        self.assertEqual(next(link for link in second.reconciliation.source_links if link.source_record_id == "cmb_1").role, "authoritative")
        self.assertEqual(next(link for link in second.reconciliation.source_links if link.source_record_id == "manual_1").role, "supporting")
        enrichment = second.enrichments_by_transaction_id[tx.id]
        self.assertEqual(enrichment.merchant_name, "咖啡店")
        self.assertEqual(enrichment.category, "餐饮美食")
        self.assertEqual(enrichment.note, "先录")

    def test_manual_duplicate_matches_previous_manual_transaction(self) -> None:
        first_entry = manual("manual_1", "2026-08-01", "20", merchant="咖啡店")
        second_entry = manual("manual_2", "2026-08-01", "20", merchant="咖啡店")
        state = build_household_domain_state((), (first_entry, second_entry), mappings())
        self.assertEqual(len(state.reconciliation.transactions), 1)
        self.assertEqual(len(state.reconciliation.source_links), 2)

    def test_ambiguous_manual_input_is_rejected(self) -> None:
        cards = (
            cmb("cmb_1", "2026-08-01", "20", "未知A"),
            cmb("cmb_2", "2026-08-02", "20", "未知B"),
        )
        entry = manual("manual_1", "2026-08-01", "20")
        with self.assertRaisesRegex(ReconciliationError, "multiple existing transactions"):
            build_household_domain_state(cards, (entry,), mappings())

    def test_merchant_can_disambiguate_same_amount_and_date_window(self) -> None:
        cards = (
            cmb("cmb_1", "2026-08-01", "20", "支付宝-咖啡"),
            cmb("cmb_2", "2026-08-02", "20", "支付宝-家电"),
        )
        entry = manual("manual_1", "2026-08-01", "20", merchant="咖啡店")
        state = build_household_domain_state(cards, (entry,), mappings())
        self.assertEqual(len(state.reconciliation.transactions), 2)
        manual_link = next(link for link in state.reconciliation.source_links if link.source_record_id == "manual_1")
        cmb_link = next(link for link in state.reconciliation.source_links if link.source_record_id == "cmb_1")
        self.assertEqual(manual_link.transaction_id, cmb_link.transaction_id)

    def test_category_never_changes_identity_matching(self) -> None:
        card = cmb("cmb_1", "2026-08-01", "20", "支付宝-咖啡")
        entry = manual("manual_1", "2026-08-01", "20", merchant="咖啡店", category="家居家电")
        state = build_household_domain_state((card,), (entry,), mappings())
        self.assertEqual(len(state.reconciliation.transactions), 1)
        tx = state.reconciliation.transactions[0]
        self.assertEqual(state.enrichments_by_transaction_id[tx.id].category, "家居家电")

    def test_cmb_ambiguous_manual_candidates_creates_own_authoritative_transaction(self) -> None:
        entries = (
            manual("manual_1", "2026-08-01", "20", merchant="咖啡店"),
            manual("manual_2", "2026-08-01", "20", merchant="京东购物"),
        )
        manual_state = build_household_domain_state((), entries, mappings())
        self.assertEqual(len(manual_state.reconciliation.transactions), 2)

        card = cmb("cmb_unknown", "2026-08-01", "20", "未映射信用卡商户")
        combined = build_household_domain_state(
            (card,),
            entries,
            mappings(),
            existing_links=manual_state.reconciliation.source_links,
        )
        self.assertEqual(len(combined.reconciliation.transactions), 3)
        cmb_link = next(
            link for link in combined.reconciliation.source_links if link.source_record_id == "cmb_unknown"
        )
        self.assertEqual(cmb_link.role, "authoritative")

    def test_cmb_only_builder_keeps_existing_identity_and_mapping_behavior(self) -> None:
        card = cmb("cmb_1", "2026-08-01", "20", "支付宝-咖啡")
        state = build_household_domain_state((card,), (), mappings())
        self.assertEqual(len(state.reconciliation.transactions), 1)
        transaction = state.reconciliation.transactions[0]
        self.assertTrue(transaction.id.startswith("txn_"))
        self.assertEqual(state.reconciliation.decisions[0].source_record_id, "cmb_1")
        self.assertEqual(state.enrichments_by_transaction_id[transaction.id].merchant_name, "咖啡店")
        self.assertEqual(state.enrichments_by_transaction_id[transaction.id].category, "餐饮美食")

    def test_cmb_rerun_reuses_transaction_identity_and_refreshes_authoritative_facts(self) -> None:
        original = cmb("cmb_1", "2026-08-01", "20", "支付宝-咖啡")
        initial = build_household_domain_state((original,), (), mappings())
        transaction_id = initial.reconciliation.transactions[0].id

        updated = cmb("cmb_1", "2026-08-02", "25", "支付宝-咖啡")
        rerun = build_household_domain_state(
            (updated,),
            (),
            mappings(),
            existing_links=initial.reconciliation.source_links,
        )
        self.assertEqual(rerun.reconciliation.transactions[0].id, transaction_id)
        self.assertEqual(rerun.reconciliation.transactions[0].transaction_date, date(2026, 8, 2))
        self.assertEqual(rerun.reconciliation.transactions[0].amount, Decimal("25"))
        self.assertEqual(rerun.reconciliation.decisions[0].action, "reused")

    def test_transaction_order_survives_manual_to_cmb_authority_switch(self) -> None:
        first_entry = manual("manual_first", "2026-08-01", "20", merchant="咖啡店")
        later_entry = manual("manual_later", "2026-08-02", "30", merchant="京东购物")
        initial = build_household_domain_state((), (first_entry, later_entry), mappings())
        initial_ids = tuple(item.id for item in initial.reconciliation.transactions)

        card = cmb("cmb_first", "2026-08-01", "20", "支付宝-咖啡")
        upgraded = build_household_domain_state(
            (card,),
            (first_entry, later_entry),
            mappings(),
            existing_links=initial.reconciliation.source_links,
        )
        self.assertEqual(tuple(item.id for item in upgraded.reconciliation.transactions), initial_ids)

    def test_income_is_kept_in_domain_but_excluded_from_spending_refund_analysis(self) -> None:
        entry = manual("manual_income", "2026-08-01", "10000", transaction_type="income", note="工资")
        state = build_household_domain_state((), (entry,), mappings())
        result = reconcile_refunds(
            state.reconciliation.transactions,
            state.source_records_by_transaction_id,
            state.enrichments_by_transaction_id,
        )
        self.assertEqual(len(state.reconciliation.transactions), 1)
        self.assertEqual(state.reconciliation.transactions[0].transaction_type, "income")
        self.assertEqual(result.net_consumption, ())
        self.assertEqual(result.refund_transactions, 0)

    def test_descriptionless_manual_refund_needs_merchant_evidence(self) -> None:
        entries = (
            manual("manual_purchase", "2026-08-01", "100"),
            manual("manual_refund", "2026-08-02", "-100"),
        )
        state = build_household_domain_state((), entries, mappings())
        result = reconcile_refunds(
            state.reconciliation.transactions,
            state.source_records_by_transaction_id,
            state.enrichments_by_transaction_id,
        )
        self.assertEqual(len(result.net_consumption), 1)
        self.assertEqual(result.unmatched_refund_count, 1)

        merchant_entries = (
            manual("manual_purchase_2", "2026-08-01", "100", merchant="咖啡店"),
            manual("manual_refund_2", "2026-08-02", "-100", merchant="咖啡店"),
        )
        merchant_state = build_household_domain_state((), merchant_entries, mappings())
        merchant_result = reconcile_refunds(
            merchant_state.reconciliation.transactions,
            merchant_state.source_records_by_transaction_id,
            merchant_state.enrichments_by_transaction_id,
        )
        self.assertEqual(merchant_result.net_consumption, ())
        self.assertEqual(merchant_result.same_merchant_refund_matches, 1)


class ManualInputPipelineTests(unittest.TestCase):
    def test_submit_validates_persists_and_invokes_downstream_generation(self) -> None:
        entry = manual("manual_cli", "2026-08-01", "30", merchant="咖啡店")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manual_path = root / "manual.jsonl"
            links_path = root / "links.jsonl"
            output_path = root / "stats.json"
            with (
                patch("family_spending.manual_input.read_transactions_csv", return_value=()),
                patch("family_spending.manual_input.load_merchant_mappings", return_value=mappings()),
                patch("family_spending.manual_input.generate_spending_statistics") as generate,
            ):
                result = submit_manual_input(
                    entry,
                    transactions_path=root / "transactions.csv",
                    manual_source_path=manual_path,
                    source_links_path=links_path,
                    merchants_path=root / "merchants.yaml",
                    categories_path=root / "categories.yaml",
                    output_path=output_path,
                    emails_dir=root / "emails",
                )

            self.assertEqual(result.source_record_id, "manual_cli")
            self.assertEqual(result.action, "created")
            self.assertEqual(read_manual_source_entries(manual_path), (entry,))
            self.assertEqual(len(read_transaction_source_links(links_path)), 1)
            generate.assert_called_once()

    def test_submit_rejects_ambiguous_input_before_persisting(self) -> None:
        entry = manual("manual_cli", "2026-08-01", "20")
        cards = (
            cmb("cmb_1", "2026-08-01", "20", "未知A"),
            cmb("cmb_2", "2026-08-02", "20", "未知B"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manual_path = root / "manual.jsonl"
            links_path = root / "links.jsonl"
            with (
                patch("family_spending.manual_input.read_transactions_csv", return_value=cards),
                patch("family_spending.manual_input.load_merchant_mappings", return_value=mappings()),
                patch("family_spending.manual_input.generate_spending_statistics") as generate,
            ):
                with self.assertRaises(ReconciliationError):
                    submit_manual_input(
                        entry,
                        transactions_path=root / "transactions.csv",
                        manual_source_path=manual_path,
                        source_links_path=links_path,
                    )
            self.assertFalse(manual_path.exists())
            self.assertFalse(links_path.exists())
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
