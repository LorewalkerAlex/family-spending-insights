from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_spending.config import AppConfig, CmbEmailSourceConfig, SourceConfig, StorageConfig
from family_spending.domain.enrichment import EnrichmentDecision
from family_spending.domain.transaction import SourceLink, build_transaction_id
from family_spending.runtime.composition import compose_runtime
from family_spending.sources.manual.model import (
    create_manual_evidence,
    manual_evidence_to_source_record,
)


class RuntimeCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve() / "household"
        self.config = AppConfig(storage=StorageConfig(self.root))

    def test_fresh_runtime_bootstraps_empty_canonical_household(self) -> None:
        components = compose_runtime(self.config)
        state = components.runtime.current_state()
        self.assertEqual(state.generation, 1)
        self.assertEqual(state.household.transactions, ())
        self.assertEqual(state.household.spending_payload["schema_version"], 2)
        self.assertEqual(state.household.financial_payload["schema_version"], 1)
        self.assertTrue(components.layout.manifest.is_file())

    def test_restart_rehydrates_manual_transaction_and_sparse_note_from_persistent_state(self) -> None:
        first = compose_runtime(self.config)
        evidence = create_manual_evidence(
            transaction_type="expense",
            transaction_date=date(2026, 8, 15),
            amount=Decimal("25.50"),
            description="cash lunch",
            evidence_id="manual_runtime_restart",
        )
        first.manual_evidence_store.replace_all((evidence,))
        record = manual_evidence_to_source_record(evidence)
        transaction_id = build_transaction_id(record)
        first.identity_store.replace(
            (SourceLink(transaction_id, record.id, "authoritative"),)
        )
        first.enrichment_store.replace(
            (EnrichmentDecision(transaction_id=transaction_id, note="remember"),)
        )

        restarted = compose_runtime(self.config)
        state = restarted.runtime.current_state()
        self.assertEqual(tuple(item.id for item in state.household.transactions), (transaction_id,))
        self.assertEqual(state.indexes.enrichment_by_transaction_id[transaction_id].note, "remember")
        self.assertEqual(state.generation, 1)

        restarted_again = compose_runtime(self.config)
        self.assertEqual(
            tuple(item.id for item in restarted_again.runtime.current_state().household.transactions),
            (transaction_id,),
        )


    def test_restart_tolerates_unreconciled_evidence_without_guessing_transaction_identity(self) -> None:
        first = compose_runtime(self.config)
        evidence = create_manual_evidence(
            transaction_type="expense",
            transaction_date=date(2026, 8, 16),
            amount=Decimal("18"),
            description="pending evidence",
            evidence_id="manual_pending_restart",
        )
        first.manual_evidence_store.replace_all((evidence,))
        record = manual_evidence_to_source_record(evidence)

        restarted = compose_runtime(self.config)
        state = restarted.runtime.current_state()
        self.assertEqual(state.household.transactions, ())
        self.assertEqual(state.household.unreconciled_source_record_ids, (record.id,))

    def test_stored_cmb_evidence_remains_registered_when_external_polling_is_disabled(self) -> None:
        disabled = AppConfig(
            storage=StorageConfig(self.root),
            sources=SourceConfig(cmb_email=CmbEmailSourceConfig(enabled=False)),
        )
        components = compose_runtime(disabled)
        self.assertEqual(
            tuple(source.source_type for source in components.source_registry.sources),
            ("cmb_email", "manual"),
        )
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            components.build_cmb_acquirer(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
