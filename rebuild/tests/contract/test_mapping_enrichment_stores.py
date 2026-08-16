from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from family_spending.domain.enrichment import EnrichmentDecision
from family_spending.domain.mapping import MappingCatalog
from family_spending.persistence.filesystem.enrichment_store import (
    EnrichmentDecisionStoreError,
    FilesystemEnrichmentDecisionStore,
)
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.mapping_store import (
    FilesystemMappingStore,
    MappingStoreError,
)


class MappingStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.layout = StorageLayout(Path(self.temp_dir.name).resolve())
        self.store = FilesystemMappingStore(self.layout)

    def test_missing_pair_represents_empty_catalog_and_round_trip_is_semantic(self) -> None:
        self.assertEqual(self.store.load(), MappingCatalog.empty())
        catalog = MappingCatalog(
            description_to_merchant={"raw-a": "Merchant A", "raw-b": "Merchant A", "raw-c": "Merchant B"},
            merchant_to_category={"Merchant A": "food", "Merchant B": "daily"},
            categories=frozenset({"food", "daily"}),
        )
        self.store.replace(catalog)
        self.assertEqual(self.store.load(), catalog)

    def test_one_missing_mapping_file_fails_closed(self) -> None:
        self.layout.merchant_mappings.parent.mkdir(parents=True)
        self.layout.merchant_mappings.write_text("Merchant:\n  - raw\n", encoding="utf-8")
        with self.assertRaisesRegex(MappingStoreError, "must exist together"):
            self.store.load()

    def test_duplicate_reviewed_keys_or_memberships_fail_closed(self) -> None:
        self.layout.merchant_mappings.parent.mkdir(parents=True)
        self.layout.merchant_mappings.write_text(
            "Merchant A:\n  - raw-a\nMerchant A:\n  - raw-b\n",
            encoding="utf-8",
        )
        self.layout.category_mappings.write_text("food:\n  - Merchant A\n", encoding="utf-8")
        with self.assertRaisesRegex(MappingStoreError, "duplicate key"):
            self.store.load()

        self.layout.merchant_mappings.write_text(
            "Merchant A:\n  - shared\nMerchant B:\n  - shared\n",
            encoding="utf-8",
        )
        self.layout.category_mappings.write_text(
            "food:\n  - Merchant A\ndaily:\n  - Merchant B\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MappingStoreError, "assigned to both"):
            self.store.load()

    def test_replacing_empty_catalog_removes_mapping_pair(self) -> None:
        catalog = MappingCatalog(
            {"raw": "Merchant"},
            {"Merchant": "food"},
            frozenset({"food"}),
        )
        self.store.replace(catalog)
        self.store.replace(MappingCatalog.empty())
        self.assertFalse(self.layout.merchant_mappings.exists())
        self.assertFalse(self.layout.category_mappings.exists())
        self.assertEqual(self.store.load(), MappingCatalog.empty())


class EnrichmentDecisionStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.layout = StorageLayout(Path(self.temp_dir.name).resolve())
        self.store = FilesystemEnrichmentDecisionStore(self.layout)

    def test_round_trip_serializes_only_actual_sparse_decisions(self) -> None:
        decisions = (
            EnrichmentDecision("txn_a", note="remember"),
            EnrichmentDecision("txn_b", merchant_override="Merchant", category_override="special"),
        )
        self.store.replace(decisions)
        self.assertEqual(self.store.load(), decisions)
        raw = [json.loads(line) for line in self.store.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(raw[0], {"transaction_id": "txn_a", "note": "remember"})
        self.assertEqual(
            raw[1],
            {
                "transaction_id": "txn_b",
                "merchant_override": "Merchant",
                "category_override": "special",
            },
        )

    def test_materialized_legacy_fields_and_duplicate_ids_fail_closed(self) -> None:
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text(
            json.dumps(
                {
                    "transaction_id": "txn_a",
                    "merchant_name": "legacy",
                    "category": "legacy",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(EnrichmentDecisionStoreError, "fields"):
            self.store.load()

        with self.assertRaisesRegex(EnrichmentDecisionStoreError, "Duplicate"):
            self.store.replace(
                (
                    EnrichmentDecision("txn_a", note="a"),
                    EnrichmentDecision("txn_a", note="b"),
                )
            )

    def test_empty_replace_removes_decision_file(self) -> None:
        self.store.replace((EnrichmentDecision("txn_a", note="a"),))
        self.store.replace(())
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.store.load(), ())


if __name__ == "__main__":
    unittest.main()
