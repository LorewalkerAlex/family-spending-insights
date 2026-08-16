from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.domain.reconciliation import (
    ReconciliationEngine,
    ReconciliationError,
    ReconciliationHints,
    ReconciliationProposal,
    ReconciliationState,
)
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import (
    SourceLink,
    SourceLinkRole,
    build_reconsidered_transaction_id,
    build_transaction_id,
)
from family_spending.sources.cmb_email.reconciliation import CmbEmailReconciliationPolicy
from family_spending.sources.manual.reconciliation import ManualReconciliationPolicy


def record(
    source_type: str,
    evidence: str,
    *,
    when: str = "2026-01-02",
    amount: str = "20",
    description: str | None = None,
) -> SourceRecord:
    return SourceRecord(
        identity=SourceIdentity(source_type, evidence, "record"),
        transaction_type="expense",
        transaction_date=date.fromisoformat(when),
        amount=Decimal(amount),
        currency="CNY",
        description=description,
    )


class FakePolicy:
    source_type = "fake"
    processing_order = 300

    def role_for_existing_link(
        self,
        record: SourceRecord,
        link: SourceLink,
        state: ReconciliationState,
    ) -> SourceLinkRole:
        del record, state
        return link.role

    def resolve_unlinked(
        self,
        record: SourceRecord,
        state: ReconciliationState,
    ) -> ReconciliationProposal:
        del record, state
        return ReconciliationProposal(None, "authoritative")


class ReconciliationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ReconciliationEngine(
            (CmbEmailReconciliationPolicy(), ManualReconciliationPolicy())
        )

    def test_cmb_first_run_creates_authoritative_transaction(self) -> None:
        cmb = record("cmb_email", "cmb-a")
        result = self.engine.reconcile((cmb,))

        self.assertEqual(result.transactions[0].id, build_transaction_id(cmb))
        self.assertEqual(
            result.source_links,
            (SourceLink(result.transactions[0].id, cmb.id, "authoritative"),),
        )
        self.assertEqual(result.decisions[0].action, "created")

    def test_existing_source_link_reuses_transaction_identity_without_rematching(self) -> None:
        manual = record("manual", "manual-a")
        preserved_transaction_id = "txn_historical"
        existing = (
            SourceLink(preserved_transaction_id, manual.id, "authoritative"),
        )
        result = self.engine.reconcile((manual,), existing_links=existing)

        self.assertEqual(result.transactions[0].id, preserved_transaction_id)
        self.assertEqual(result.decisions[0].action, "reused")
        self.assertTrue(result.decisions[0].evidence.source_identity_match)

    def test_existing_link_wins_even_when_current_matching_would_choose_another_transaction(self) -> None:
        cmb = record("cmb_email", "cmb-a")
        manual = record("manual", "manual-a")
        existing = (
            SourceLink("txn_cmb", cmb.id, "authoritative"),
            SourceLink("txn_manual_history", manual.id, "authoritative"),
        )

        result = self.engine.reconcile((cmb, manual), existing_links=existing)

        manual_link = next(
            item for item in result.source_links if item.source_record_id == manual.id
        )
        self.assertEqual(manual_link.transaction_id, "txn_manual_history")
        decision = next(
            item for item in result.decisions if item.source_record_id == manual.id
        )
        self.assertEqual(decision.action, "reused")

    def test_fake_source_policy_plugs_into_generic_engine_without_central_branch(self) -> None:
        fake = record("fake", "fake-a")
        result = ReconciliationEngine((FakePolicy(),)).reconcile((fake,))
        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.source_links[0].source_record_id, fake.id)

    def test_manual_matches_existing_cmb_as_supporting(self) -> None:
        cmb = record("cmb_email", "cmb-a")
        initial = self.engine.reconcile((cmb,))
        manual = record("manual", "manual-a")
        result = self.engine.reconcile(
            (cmb, manual),
            existing_links=initial.source_links,
        )

        transaction_id = initial.transactions[0].id
        self.assertEqual(len(result.transactions), 1)
        self.assertIn(SourceLink(transaction_id, cmb.id, "authoritative"), result.source_links)
        self.assertIn(SourceLink(transaction_id, manual.id, "supporting"), result.source_links)
        manual_decision = next(
            item for item in result.decisions if item.source_record_id == manual.id
        )
        self.assertEqual(manual_decision.action, "matched")

    def test_manual_ambiguous_existing_transactions_fail_fast(self) -> None:
        cmb_a = record("cmb_email", "cmb-a")
        cmb_b = record("cmb_email", "cmb-b")
        existing = (
            SourceLink("txn_a", cmb_a.id, "authoritative"),
            SourceLink("txn_b", cmb_b.id, "authoritative"),
        )
        manual = record("manual", "manual-a")

        with self.assertRaisesRegex(ReconciliationError, "matches multiple Transactions"):
            self.engine.reconcile(
                (cmb_a, cmb_b, manual),
                existing_links=existing,
            )

    def test_unique_merchant_hint_disambiguates_manual_candidate(self) -> None:
        cmb_a = record("cmb_email", "cmb-a")
        cmb_b = record("cmb_email", "cmb-b")
        existing = (
            SourceLink("txn_a", cmb_a.id, "authoritative"),
            SourceLink("txn_b", cmb_b.id, "authoritative"),
        )
        manual = record("manual", "manual-a")
        hints = ReconciliationHints(
            merchant_by_transaction_id={"txn_a": "A", "txn_b": "B"},
            merchant_by_source_record_id={manual.id: "B"},
        )

        result = self.engine.reconcile(
            (cmb_a, cmb_b, manual),
            existing_links=existing,
            hints=hints,
        )
        link = next(item for item in result.source_links if item.source_record_id == manual.id)
        self.assertEqual(link.transaction_id, "txn_b")
        self.assertEqual(link.role, "supporting")

    def test_cmb_takes_authority_from_unique_manual_backed_transaction(self) -> None:
        manual = record("manual", "manual-a")
        existing = (SourceLink("txn_manual", manual.id, "authoritative"),)
        cmb = record("cmb_email", "cmb-a")

        result = self.engine.reconcile(
            (manual, cmb),
            existing_links=existing,
        )

        self.assertEqual(len(result.transactions), 1)
        self.assertEqual(result.transactions[0].id, "txn_manual")
        self.assertIn(SourceLink("txn_manual", manual.id, "supporting"), result.source_links)
        self.assertIn(SourceLink("txn_manual", cmb.id, "authoritative"), result.source_links)
        self.assertEqual(result.transactions[0].amount, cmb.amount)

    def test_cmb_ambiguous_manual_candidates_create_separate_transaction(self) -> None:
        manual_a = record("manual", "manual-a")
        manual_b = record("manual", "manual-b")
        existing = (
            SourceLink("txn_manual_a", manual_a.id, "authoritative"),
            SourceLink("txn_manual_b", manual_b.id, "authoritative"),
        )
        cmb = record("cmb_email", "cmb-a")

        result = self.engine.reconcile(
            (manual_a, manual_b, cmb),
            existing_links=existing,
        )

        cmb_link = next(item for item in result.source_links if item.source_record_id == cmb.id)
        self.assertEqual(cmb_link.transaction_id, build_transaction_id(cmb))
        self.assertEqual(cmb_link.role, "authoritative")
        self.assertEqual(len(result.transactions), 3)

    def test_policy_processing_order_keeps_cmb_before_manual_regardless_input_order(self) -> None:
        cmb_a = record("cmb_email", "cmb-a")
        cmb_b = record("cmb_email", "cmb-b")
        manual = record("manual", "manual-a")

        with self.assertRaises(ReconciliationError):
            self.engine.reconcile((manual, cmb_a, cmb_b))
        with self.assertRaises(ReconciliationError):
            self.engine.reconcile((cmb_a, cmb_b, manual))

    def test_missing_linked_source_fails_instead_of_silently_dropping_identity_history(self) -> None:
        missing = SourceLink("txn_missing", "src_missing", "authoritative")
        with self.assertRaisesRegex(ReconciliationError, "missing SourceRecord"):
            self.engine.reconcile((), existing_links=(missing,))

    def test_source_removal_promotes_surviving_support_without_changing_transaction_id(self) -> None:
        manual_authority = record("manual", "manual-authority")
        manual_support = record("manual", "manual-support")
        surviving = (manual_support,)
        transient_links = (
            SourceLink("txn_preserved", manual_support.id, "supporting"),
        )

        repaired = self.engine.recover_authority_after_source_removal(
            surviving,
            transient_links,
        )
        self.assertEqual(
            repaired,
            (SourceLink("txn_preserved", manual_support.id, "authoritative"),),
        )
        result = self.engine.reconcile(surviving, existing_links=repaired)
        self.assertEqual(result.transactions[0].id, "txn_preserved")

    def test_source_removal_uses_policy_order_when_multiple_supports_survive(self) -> None:
        cmb = record("cmb_email", "cmb-support")
        manual = record("manual", "manual-support")
        repaired = self.engine.recover_authority_after_source_removal(
            (manual, cmb),
            (
                SourceLink("txn_preserved", manual.id, "supporting"),
                SourceLink("txn_preserved", cmb.id, "supporting"),
            ),
        )
        self.assertIn(
            SourceLink("txn_preserved", cmb.id, "authoritative"),
            repaired,
        )
        self.assertIn(
            SourceLink("txn_preserved", manual.id, "supporting"),
            repaired,
        )


    def test_reconsidered_standalone_source_reuses_its_previous_transaction_identity(self) -> None:
        original = record("manual", "manual-corrected", amount="20")
        corrected = record("manual", "manual-corrected", amount="30")
        result = self.engine.reconcile_reconsidered_source(
            (corrected,),
            existing_links=(
                SourceLink("txn_historical", original.id, "authoritative"),
            ),
            source_record_id=corrected.id,
        )

        self.assertEqual(result.transactions[0].id, "txn_historical")
        self.assertEqual(result.transactions[0].amount, Decimal("30"))
        self.assertEqual(result.decisions[0].action, "reused")

    def test_reconsidered_source_splits_without_colliding_with_surviving_old_transaction(self) -> None:
        cmb = record("cmb_email", "cmb-old", amount="20")
        original_manual = record("manual", "manual-split", amount="20")
        corrected_manual = record("manual", "manual-split", amount="30")
        existing = (
            SourceLink("txn_preserved", cmb.id, "authoritative"),
            SourceLink("txn_preserved", original_manual.id, "supporting"),
        )

        result = self.engine.reconcile_reconsidered_source(
            (cmb, corrected_manual),
            existing_links=existing,
            source_record_id=corrected_manual.id,
        )
        split_id = build_reconsidered_transaction_id(
            corrected_manual,
            "txn_preserved",
        )

        self.assertIn(
            SourceLink("txn_preserved", cmb.id, "authoritative"),
            result.source_links,
        )
        self.assertIn(
            SourceLink(split_id, corrected_manual.id, "authoritative"),
            result.source_links,
        )
        self.assertNotEqual(split_id, "txn_preserved")
        self.assertEqual({item.id for item in result.transactions}, {"txn_preserved", split_id})

    def test_reconsidered_authority_promotes_surviving_support_before_split(self) -> None:
        original_authority = record("manual", "manual-authority", amount="20")
        corrected_authority = record("manual", "manual-authority", amount="30")
        support = record("manual", "manual-support", amount="20")
        existing = (
            SourceLink("txn_old", original_authority.id, "authoritative"),
            SourceLink("txn_old", support.id, "supporting"),
        )

        result = self.engine.reconcile_reconsidered_source(
            (corrected_authority, support),
            existing_links=existing,
            source_record_id=corrected_authority.id,
        )

        self.assertIn(
            SourceLink("txn_old", support.id, "authoritative"),
            result.source_links,
        )
        corrected_link = next(
            item
            for item in result.source_links
            if item.source_record_id == corrected_authority.id
        )
        self.assertEqual(
            corrected_link.transaction_id,
            build_reconsidered_transaction_id(corrected_authority, "txn_old"),
        )
        self.assertEqual(corrected_link.role, "authoritative")

    def test_reconsidered_manual_can_converge_to_another_existing_transaction(self) -> None:
        original = record("manual", "manual-converge", amount="20")
        corrected = record("manual", "manual-converge", amount="30")
        cmb = record("cmb_email", "cmb-target", amount="30")
        existing = (
            SourceLink("txn_old", original.id, "authoritative"),
            SourceLink("txn_target", cmb.id, "authoritative"),
        )

        result = self.engine.reconcile_reconsidered_source(
            (corrected, cmb),
            existing_links=existing,
            source_record_id=corrected.id,
        )

        corrected_link = next(
            item for item in result.source_links if item.source_record_id == corrected.id
        )
        self.assertEqual(corrected_link.transaction_id, "txn_target")
        self.assertEqual(corrected_link.role, "supporting")
        self.assertEqual(tuple(item.id for item in result.transactions), ("txn_target",))
        corrected_decision = next(
            item for item in result.decisions if item.source_record_id == corrected.id
        )
        self.assertEqual(corrected_decision.action, "matched")


if __name__ == "__main__":
    unittest.main()
