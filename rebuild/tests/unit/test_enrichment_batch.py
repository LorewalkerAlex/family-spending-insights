from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.domain.enrichment import (
    EnrichmentDecision,
    resolve_enrichments,
)
from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import transaction_from_source_record


class EnrichmentBatchTests(unittest.TestCase):
    def source(self, key: str, description: str) -> SourceRecord:
        return SourceRecord(
            SourceIdentity("manual", key, "record"),
            "expense",
            date(2026, 1, 2),
            Decimal("10"),
            "CNY",
            description,
        )

    def test_batch_preserves_transaction_order_and_sparse_decisions(self) -> None:
        mappings = MappingCatalog(
            {"raw-a": "A", "raw-b": "B"},
            {"A": "food", "B": "daily"},
            frozenset({"food", "daily"}),
        )
        sources = (self.source("a", "raw-a"), self.source("b", "raw-b"))
        transactions = tuple(transaction_from_source_record(item) for item in sources)
        source_index = dict(zip((item.id for item in transactions), sources, strict=True))
        decisions = (EnrichmentDecision(transactions[1].id, note="keep"),)

        resolved = resolve_enrichments(transactions, source_index, mappings, decisions)

        self.assertEqual(tuple(item.transaction_id for item in resolved), tuple(item.id for item in transactions))
        self.assertIsNone(resolved[0].note)
        self.assertEqual(resolved[1].note, "keep")

    def test_orphan_decision_and_missing_authoritative_source_fail_closed(self) -> None:
        source = self.source("a", "raw-a")
        transaction = transaction_from_source_record(source)
        mappings = MappingCatalog(
            {"raw-a": "A"},
            {"A": "food"},
            frozenset({"food"}),
        )
        with self.assertRaisesRegex(DomainInvariantError, "missing Transactions"):
            resolve_enrichments(
                (transaction,),
                {transaction.id: source},
                mappings,
                (EnrichmentDecision("txn_missing", note="stale"),),
            )
        with self.assertRaisesRegex(DomainInvariantError, "no authoritative SourceRecord"):
            resolve_enrichments((transaction,), {}, mappings)


if __name__ == "__main__":
    unittest.main()
