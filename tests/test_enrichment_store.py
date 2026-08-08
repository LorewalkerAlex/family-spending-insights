from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from family_spending.enrichment import TransactionEnrichmentState
from family_spending.enrichment_store import (
    EnrichmentStateStoreError,
    read_enrichment_states,
    write_enrichment_states,
)


class EnrichmentStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "enrichment_state.jsonl"

    def test_missing_file_is_empty_and_round_trip_is_exact(self) -> None:
        self.assertEqual(read_enrichment_states(self.path), ())
        states = (
            TransactionEnrichmentState(
                transaction_id="txn_1",
                merchant_name="测试商户",
                default_category="餐饮美食",
                category="餐饮美食",
                category_source="merchant_default",
                note=None,
            ),
            TransactionEnrichmentState(
                transaction_id="txn_2",
                merchant_name=None,
                default_category=None,
                category="待分类",
                category_source="unclassified",
                note="待确认",
            ),
        )
        write_enrichment_states(states, self.path)
        self.assertEqual(read_enrichment_states(self.path), states)

    def test_duplicate_transaction_id_is_rejected(self) -> None:
        self.path.write_text(
            '{"transaction_id":"txn_1","merchant_name":null,"default_category":null,'
            '"category":"待分类","category_source":"unclassified","note":null}\n'
            '{"transaction_id":"txn_1","merchant_name":null,"default_category":null,'
            '"category":"待分类","category_source":"unclassified","note":null}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(EnrichmentStateStoreError, "Duplicate transaction_id"):
            read_enrichment_states(self.path)

    def test_default_category_without_merchant_is_rejected(self) -> None:
        self.path.write_text(
            '{"transaction_id":"txn_1","merchant_name":null,"default_category":"餐饮美食",'
            '"category":"家居家电","category_source":"manual_override","note":null}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(EnrichmentStateStoreError, "requires merchant_name"):
            read_enrichment_states(self.path)

    def test_inconsistent_merchant_default_is_rejected(self) -> None:
        self.path.write_text(
            '{"transaction_id":"txn_1","merchant_name":"测试商户","default_category":"餐饮美食",'
            '"category":"家居家电","category_source":"merchant_default","note":null}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(EnrichmentStateStoreError, "must equal default_category"):
            read_enrichment_states(self.path)


if __name__ == "__main__":
    unittest.main()
