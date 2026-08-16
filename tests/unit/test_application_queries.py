from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from family_spending.application.queries import QueryService
from family_spending.domain.enrichment import ResolvedEnrichment
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import SourceLink, Transaction


class _SingleReadRuntime:
    def __init__(self, state) -> None:
        self._state = state
        self.calls = 0

    def current_state(self):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("one query must not mix Runtime generations")
        return self._state


class ApplicationQueryTests(unittest.TestCase):
    @staticmethod
    def _state():
        source = SourceRecord(
            identity=SourceIdentity("manual", "manual_query", "record"),
            transaction_type="expense",
            transaction_date=date(2026, 8, 16),
            amount=Decimal("12.50"),
            currency="CNY",
            description="query merchant",
        )
        transaction = Transaction(
            id="txn_query",
            transaction_type="expense",
            transaction_date=source.transaction_date,
            amount=source.amount,
            currency=source.currency,
        )
        enrichment = ResolvedEnrichment(
            transaction_id=transaction.id,
            merchant_name=None,
            display_name="query merchant",
            default_category=None,
            category="待分类",
            category_source="unclassified",
            is_unclassified=True,
            review_signals=(),
        )
        link = SourceLink(transaction.id, source.id, "authoritative")
        household = SimpleNamespace(
            transactions=(transaction,),
            source_records=(source,),
            source_links=(link,),
            mappings=SimpleNamespace(categories=frozenset()),
            spending_payload={"schema_version": 2},
            financial_payload={"schema_version": 1},
        )
        indexes = SimpleNamespace(
            transaction_by_id={transaction.id: transaction},
            authoritative_source_by_transaction_id={transaction.id: source},
            enrichment_by_transaction_id={transaction.id: enrichment},
            transaction_ids_by_review_signal={},
        )
        return SimpleNamespace(household=household, indexes=indexes)

    def test_transaction_list_uses_one_immutable_runtime_generation(self) -> None:
        runtime = _SingleReadRuntime(self._state())
        views = QueryService(runtime=runtime).list_transactions()
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].transaction.id, "txn_query")
        self.assertEqual(runtime.calls, 1)

    def test_manual_input_list_uses_the_same_snapshot_for_source_and_transaction(self) -> None:
        runtime = _SingleReadRuntime(self._state())
        views = QueryService(runtime=runtime).list_manual_inputs()
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].evidence_id, "manual_query")
        self.assertEqual(views[0].transaction_id, "txn_query")
        self.assertEqual(runtime.calls, 1)


if __name__ == "__main__":
    unittest.main()
