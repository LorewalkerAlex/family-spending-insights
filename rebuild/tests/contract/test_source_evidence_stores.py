from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_spending.persistence.filesystem.cmb_email_evidence_store import (
    CmbEmailEvidenceStore,
    CmbEmailEvidenceStoreError,
)
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.manual_evidence_store import (
    ManualEvidenceStore,
    ManualEvidenceStoreError,
)
from family_spending.persistence.filesystem.manifest import initialize_storage
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence
from family_spending.sources.manual.model import create_manual_evidence


class SourceEvidenceStoreContractTests(unittest.TestCase):
    def layout(self, root: Path) -> StorageLayout:
        layout = StorageLayout(root.resolve())
        initialize_storage(layout)
        return layout

    def test_cmb_store_is_content_addressed_and_retry_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = self.layout(Path(temp_dir) / "household")
            store = CmbEmailEvidenceStore(layout)
            evidence = CmbEmailEvidence(b"raw-eml-bytes")

            self.assertTrue(store.add(evidence))
            self.assertFalse(store.add(evidence))
            self.assertEqual(store.load_all(), (evidence,))
            persisted = layout.cmb_email_evidence / evidence.filename
            self.assertEqual(persisted.read_bytes(), evidence.raw_bytes)

    def test_cmb_store_rejects_filename_content_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = self.layout(Path(temp_dir) / "household")
            store = CmbEmailEvidenceStore(layout)
            wrong_name = CmbEmailEvidence(b"name-source").filename
            (layout.cmb_email_evidence / wrong_name).write_bytes(b"different-content")

            with self.assertRaisesRegex(CmbEmailEvidenceStoreError, "filename does not match"):
                store.load_all()

    def test_manual_store_round_trips_only_source_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = self.layout(Path(temp_dir) / "household")
            store = ManualEvidenceStore(layout)
            evidence = create_manual_evidence(
                transaction_type="expense",
                transaction_date=date(2026, 8, 15),
                amount=Decimal("18.50"),
                description="breakfast",
                evidence_id="manual_breakfast",
            )
            store.replace_all((evidence,))
            self.assertEqual(store.load_all(), (evidence,))

            payload = json.loads(layout.manual_evidence.read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload),
                {"id", "type", "date", "amount", "currency", "description"},
            )
            self.assertNotIn("merchant", payload)
            self.assertNotIn("category", payload)
            self.assertNotIn("note", payload)

    def test_manual_store_rejects_legacy_enrichment_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = self.layout(Path(temp_dir) / "household")
            payload = {
                "id": "manual_legacy",
                "type": "expense",
                "date": "2026-08-15",
                "amount": "1.00",
                "currency": "CNY",
                "description": "test",
                "merchant": "legacy",
            }
            layout.manual_evidence.write_text(
                json.dumps(payload, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ManualEvidenceStoreError, "unknown fields"):
                ManualEvidenceStore(layout).load_all()

    def test_manual_store_duplicate_identity_fails_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = self.layout(Path(temp_dir) / "household")
            store = ManualEvidenceStore(layout)
            first = create_manual_evidence(
                transaction_type="expense",
                transaction_date=date(2026, 8, 15),
                amount=Decimal("1.00"),
                evidence_id="manual_same",
            )
            second = create_manual_evidence(
                transaction_type="expense",
                transaction_date=date(2026, 8, 16),
                amount=Decimal("2.00"),
                evidence_id="manual_same",
            )
            with self.assertRaisesRegex(ManualEvidenceStoreError, "duplicate ids"):
                store.replace_all((first, second))
            self.assertFalse(layout.manual_evidence.exists())


if __name__ == "__main__":
    unittest.main()
