from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.application.enrichment import UNSET
from family_spending.application.errors import ApplicationConflictError
from family_spending.config import AppConfig, StorageConfig
from family_spending.domain.mapping import MappingCatalog
from family_spending.runtime.composition import compose_runtime


class ApplicationUseCaseIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve() / "household"
        self.config = AppConfig(storage=StorageConfig(self.root))

        seed = compose_runtime(self.config)
        seed.mapping_store.replace(
            MappingCatalog(
                description_to_merchant={
                    "known": "Known Merchant",
                    "appliance": "Home Merchant",
                },
                merchant_to_category={
                    "Known Merchant": "餐饮美食",
                    "Home Merchant": "家居家电",
                },
                categories=frozenset({"餐饮美食", "家居家电"}),
            )
        )
        self.components = compose_runtime(self.config)
        self.application = self.components.application

    def test_manual_enrichment_mapping_schedule_feedback_and_restart_share_one_application(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="known",
            note="manual note",
        )
        transaction_id = created.transaction.transaction.id
        self.assertEqual(created.action, "created")
        self.assertEqual(created.transaction.enrichment.merchant_name, "Known Merchant")
        self.assertEqual(created.transaction.enrichment.category, "餐饮美食")
        self.assertEqual(created.transaction.enrichment.note, "manual note")

        overridden = self.application.update_enrichment(
            transaction_id,
            merchant="One-off Merchant",
            category="餐饮美食",
            note="workspace note",
        )
        self.assertEqual(overridden.enrichment.merchant_name, "One-off Merchant")
        self.assertEqual(overridden.enrichment.category_source, "transaction_override")

        corrected = self.application.correct_manual_input(
            created.evidence_id,
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="22",
            description="known",
            note=UNSET,
        )
        self.assertEqual(corrected.transaction.transaction.id, transaction_id)
        self.assertEqual(corrected.transaction.enrichment.merchant_name, "One-off Merchant")
        self.assertEqual(corrected.transaction.enrichment.note, "workspace note")

        unmapped = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-15",
            amount="31",
            description="needs review",
        )
        workspace = self.application.get_mapping_review_workspace()
        self.assertEqual(tuple(item.description for item in workspace.items), ("needs review",))
        preview = self.application.preview_mapping_review(
            description="needs review",
            merchant="Reviewed Merchant",
            category="餐饮美食",
        )
        with self.assertRaisesRegex(Exception, "confirmation"):
            self.application.apply_mapping_review(
                description="needs review",
                merchant="Reviewed Merchant",
                category="餐饮美食",
                preview_token=preview.token,
            )
        self.application.apply_mapping_review(
            description="needs review",
            merchant="Reviewed Merchant",
            category="餐饮美食",
            preview_token=preview.token,
            confirm_new_merchant=True,
        )
        reviewed = self.application.get_transaction(unmapped.transaction.transaction.id)
        self.assertEqual(reviewed.enrichment.merchant_name, "Reviewed Merchant")
        self.assertEqual(self.application.get_mapping_review_workspace().items, ())

        rule = self.application.create_scheduled_input(
            transaction_type="expense",
            amount="77",
            description="monthly fixed",
            next_date="2026-01-10",
            note="scheduled note",
            as_of=date(2026, 3, 10),
        )
        self.assertEqual(rule.first_occurrence_date, date(2026, 1, 10))
        self.assertEqual(
            self.components.schedule_store.load_execution()[0].last_processed_occurrence_date,
            date(2026, 3, 10),
        )
        scheduled_evidence_count = sum(
            item.evidence_id.startswith("schedule_occ_")
            for item in self.components.manual_evidence_store.load_all()
        )
        self.assertEqual(scheduled_evidence_count, 3)
        self.assertEqual(
            self.application.run_due_scheduled_inputs(date(2026, 3, 10)).occurrences,
            (),
        )

        # Cursor loss is recoverable from stable occurrence identity without duplicate facts.
        self.components.schedule_store.replace_execution(())
        recovered = self.application.run_due_scheduled_inputs(date(2026, 3, 10))
        self.assertEqual(len(recovered.occurrences), 3)
        self.assertTrue(all(item.action == "recovered" for item in recovered.occurrences))
        self.assertEqual(
            sum(
                item.evidence_id.startswith("schedule_occ_")
                for item in self.components.manual_evidence_store.load_all()
            ),
            3,
        )

        feedback = self.application.create_feedback(
            content="Overview needs polish",
            context={"runtime": "desktop_web", "page": "overview"},
        )
        self.assertEqual(feedback.status, "open")
        self.assertEqual(
            self.application.update_feedback(feedback.id, status="resolved").status,
            "resolved",
        )

        spending = self.application.get_spending_statistics()
        financial = self.application.get_financial_summary()
        self.assertEqual(spending["schema_version"], 2)
        self.assertEqual(financial["schema_version"], 1)

        restarted = compose_runtime(self.config)
        self.assertEqual(
            {item.transaction.id for item in restarted.application.list_transactions()},
            {item.transaction.id for item in self.application.list_transactions()},
        )
        self.assertEqual(restarted.application.list_scheduled_inputs(), (rule,))
        self.assertEqual(restarted.application.list_feedback()[0].status, "resolved")

    def test_income_bypasses_expense_mapping_and_flows_into_financial_projection(self) -> None:
        income = self.application.create_manual_input(
            transaction_type="income",
            transaction_date="2026-01-06",
            amount="1000",
            description="salary",
            note="工资",
        )
        enrichment = income.transaction.enrichment
        self.assertIsNone(enrichment.merchant_name)
        self.assertIsNone(enrichment.default_category)
        self.assertEqual(enrichment.category, "其他收入")
        self.assertEqual(enrichment.category_source, "income_default")
        self.assertEqual(enrichment.note, "工资")
        january = next(
            row
            for row in self.application.get_financial_summary()["months"]
            if row["month"] == "2026-01"
        )
        self.assertEqual(january["total_income_minor"], 100_000)

    def test_mapping_apply_preserves_explicit_transaction_category_override(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-20",
            amount="120",
            description="override review",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(
            transaction_id,
            category="家居家电",
        )
        preview = self.application.preview_mapping_review(
            description="override review",
            merchant="Known Merchant",
            category="餐饮美食",
        )
        self.application.apply_mapping_review(
            description="override review",
            merchant="Known Merchant",
            category="餐饮美食",
            preview_token=preview.token,
        )
        refreshed = self.application.get_transaction(transaction_id)
        self.assertEqual(refreshed.enrichment.merchant_name, "Known Merchant")
        self.assertEqual(refreshed.enrichment.category, "家居家电")
        self.assertEqual(refreshed.enrichment.category_source, "transaction_override")

    def test_enrichment_failure_after_write_rolls_back_decision_and_runtime_generation(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-25",
            amount="22",
            description="known",
        )
        transaction_id = created.transaction.transaction.id
        decision_path = self.components.layout.enrichment_decisions
        before = decision_path.read_bytes() if decision_path.exists() else None
        generation_before = self.components.runtime.current_state().generation
        with patch.object(
            type(self.components.snapshot_builder),
            "build",
            autospec=True,
            side_effect=OSError("candidate rebuild failed"),
        ):
            with self.assertRaisesRegex(OSError, "candidate rebuild failed"):
                self.application.update_enrichment(
                    transaction_id,
                    note="must roll back",
                )
        after = decision_path.read_bytes() if decision_path.exists() else None
        self.assertEqual(after, before)
        self.assertEqual(
            self.components.runtime.current_state().generation,
            generation_before,
        )
        self.assertIsNone(self.application.get_transaction(transaction_id).enrichment.note)

    def test_source_sync_reconciles_pending_persistent_evidence_through_application(self) -> None:
        from family_spending.sources.manual.model import create_manual_evidence

        evidence = create_manual_evidence(
            transaction_type="expense",
            transaction_date=date(2026, 2, 10),
            amount=Decimal("33"),
            description="known",
            evidence_id="manual_pending_application",
        )
        self.components.manual_evidence_store.replace_all((evidence,))
        restarted = compose_runtime(self.config)
        before = restarted.runtime.current_state()
        self.assertEqual(before.household.transactions, ())
        self.assertEqual(len(before.household.unreconciled_source_record_ids), 1)

        result = restarted.application.sync_sources()
        self.assertEqual(result.created_count, 1)
        after = restarted.runtime.current_state()
        self.assertEqual(len(after.household.transactions), 1)
        self.assertEqual(after.generation, before.generation + 1)

    def test_schedule_rule_crud_and_due_failure_share_coordinator_and_uow(self) -> None:
        future = self.application.create_scheduled_input(
            transaction_type="income",
            amount="15000",
            description="salary",
            next_date="2099-01-06",
            enabled=True,
            as_of=date(2026, 8, 16),
        )
        updated = self.application.update_scheduled_input(
            future.id,
            transaction_type="income",
            amount="16000",
            description="salary",
            next_date="2099-02-06",
            enabled=False,
            as_of=date(2026, 8, 16),
        )
        self.assertEqual(updated.amount, Decimal("16000"))
        self.assertFalse(updated.enabled)
        self.assertEqual(self.application.delete_scheduled_input(updated.id).id, updated.id)

        generation_before = self.components.runtime.current_state().generation
        with patch.object(
            type(self.components.identity_store),
            "replace",
            autospec=True,
            side_effect=OSError("identity write failed"),
        ):
            with self.assertRaisesRegex(OSError, "identity write failed"):
                self.application.create_scheduled_input(
                    transaction_type="expense",
                    amount="10",
                    description="due failure",
                    next_date="2026-08-16",
                    as_of=date(2026, 8, 16),
                )
        self.assertEqual(self.components.schedule_store.load_rules(), ())
        self.assertEqual(self.components.schedule_store.load_execution(), ())
        self.assertEqual(self.components.manual_evidence_store.load_all(), ())
        self.assertEqual(
            self.components.runtime.current_state().generation,
            generation_before,
        )

    def test_mapping_preview_is_revalidated_after_an_intervening_financial_mutation(self) -> None:
        self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-02-01",
            amount="41",
            description="stale review",
        )
        preview = self.application.preview_mapping_review(
            description="stale review",
            merchant="Known Merchant",
            category="餐饮美食",
        )
        self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-02-02",
            amount="42",
            description="stale review",
        )
        with self.assertRaises(ApplicationConflictError):
            self.application.apply_mapping_review(
                description="stale review",
                merchant="Known Merchant",
                category="餐饮美食",
                preview_token=preview.token,
            )

    def test_manual_failure_rolls_back_evidence_identity_and_runtime_generation(self) -> None:
        generation_before = self.components.runtime.current_state().generation
        with patch.object(
            type(self.components.enrichment_store),
            "replace",
            autospec=True,
            side_effect=OSError("decision write failed"),
        ):
            with self.assertRaisesRegex(OSError, "decision write failed"):
                self.application.create_manual_input(
                    transaction_type="expense",
                    transaction_date="2026-03-01",
                    amount="55",
                    description="known",
                    note="must roll back",
                )
        self.assertEqual(self.components.manual_evidence_store.load_all(), ())
        self.assertEqual(self.components.identity_store.load(), ())
        self.assertEqual(self.components.runtime.current_state().generation, generation_before)

    def test_delete_supporting_manual_evidence_preserves_existing_transaction(self) -> None:
        first = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-04-01",
            amount="88",
            description="known",
        )
        second = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-04-01",
            amount="88",
            description="known",
        )
        self.assertEqual(second.action, "matched")
        self.assertEqual(first.transaction.transaction.id, second.transaction.transaction.id)
        deletion = self.application.delete_manual_input(second.evidence_id)
        self.assertFalse(deletion.transaction_removed)
        self.assertEqual(
            self.application.get_transaction(first.transaction.transaction.id).transaction.id,
            first.transaction.transaction.id,
        )

    def test_manual_correction_can_split_from_or_converge_to_existing_transaction_without_id_collision(self) -> None:
        original = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-04-10",
            amount="60",
            description="known",
        )
        original_id = original.transaction.transaction.id
        support = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-04-10",
            amount="60",
            description="known",
        )
        self.assertEqual(support.transaction.transaction.id, original_id)

        split = self.application.correct_manual_input(
            original.evidence_id,
            transaction_type="expense",
            transaction_date="2026-04-11",
            amount="61",
            description="known",
        )
        self.assertNotEqual(split.transaction.transaction.id, original_id)
        remaining = next(
            item for item in self.application.list_manual_inputs()
            if item.evidence_id == support.evidence_id
        )
        self.assertEqual(remaining.transaction_id, original_id)
        self.assertEqual(remaining.source_role, "authoritative")

        target = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-04-20",
            amount="75",
            description="known",
        )
        converged = self.application.correct_manual_input(
            original.evidence_id,
            transaction_type="expense",
            transaction_date="2026-04-20",
            amount="75",
            description="known",
        )
        self.assertEqual(converged.action, "matched")
        self.assertEqual(
            converged.transaction.transaction.id,
            target.transaction.transaction.id,
        )

    def test_delete_authoritative_manual_promotes_surviving_support_without_identity_drift(self) -> None:
        first = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-05-01",
            amount="99",
            description="known",
        )
        second = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-05-01",
            amount="99",
            description="known",
        )
        transaction_id = first.transaction.transaction.id
        self.assertEqual(second.transaction.transaction.id, transaction_id)

        deletion = self.application.delete_manual_input(first.evidence_id)
        self.assertFalse(deletion.transaction_removed)
        remaining = next(
            item
            for item in self.application.list_manual_inputs()
            if item.evidence_id == second.evidence_id
        )
        self.assertEqual(remaining.transaction_id, transaction_id)
        self.assertEqual(remaining.source_role, "authoritative")



if __name__ == "__main__":
    unittest.main()
