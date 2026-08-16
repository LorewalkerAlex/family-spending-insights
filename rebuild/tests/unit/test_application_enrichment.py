from __future__ import annotations

import unittest

from family_spending.application.enrichment import update_decision_collection
from family_spending.domain.enrichment import EnrichmentDecision


class ApplicationEnrichmentTests(unittest.TestCase):
    def test_sparse_update_preserves_unmentioned_fields_and_removes_all_null_decision(self) -> None:
        current = (
            EnrichmentDecision(
                transaction_id="txn_1",
                merchant_override="Merchant",
                category_override="餐饮美食",
                note="keep",
            ),
        )
        updated = update_decision_collection(current, "txn_1", note="changed")
        self.assertEqual(updated[0].merchant_override, "Merchant")
        self.assertEqual(updated[0].category_override, "餐饮美食")
        self.assertEqual(updated[0].note, "changed")

        cleared = update_decision_collection(
            updated,
            "txn_1",
            merchant=None,
            category=None,
            note=None,
        )
        self.assertEqual(cleared, ())


if __name__ == "__main__":
    unittest.main()
