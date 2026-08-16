from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from types import MappingProxyType

from family_spending.application.source_registry import SourceRegistry
from family_spending.domain.enrichment import GENERAL_SHOPPING_CATEGORY, HIGH_VALUE_GENERAL_SHOPPING_REVIEW
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import SourceLink, build_transaction_id
from family_spending.runtime.state import RuntimeBuildError, RuntimeSnapshotBuilder, RuntimeStore


class _Source:
    source_type = "manual"

    def __init__(self, records: tuple[SourceRecord, ...]) -> None:
        self.records = records

    def load_records(self) -> tuple[SourceRecord, ...]:
        return self.records


class _IdentityStore:
    def __init__(self, links: tuple[SourceLink, ...]) -> None:
        self.links = links

    def load(self) -> tuple[SourceLink, ...]:
        return self.links


class _MappingStore:
    def __init__(self, mappings: MappingCatalog) -> None:
        self.mappings = mappings

    def load(self) -> MappingCatalog:
        return self.mappings


class _EnrichmentStore:
    def load(self):
        return ()


class _StatementDates:
    def load_statement_dates_by_evidence(self):
        return {"manual-runtime": date(2026, 9, 10)}


def _record() -> SourceRecord:
    return SourceRecord(
        identity=SourceIdentity("manual", "manual-runtime", "record"),
        transaction_type="expense",
        transaction_date=date(2026, 8, 12),
        amount=Decimal("1200"),
        currency="CNY",
        description="raw shop",
    )


def _builder(*, linked: bool = True) -> RuntimeSnapshotBuilder:
    record = _record()
    links = (
        (SourceLink(build_transaction_id(record), record.id, "authoritative"),)
        if linked
        else ()
    )
    mappings = MappingCatalog(
        {"raw shop": "Shop"},
        {"Shop": GENERAL_SHOPPING_CATEGORY},
        frozenset({GENERAL_SHOPPING_CATEGORY}),
    )
    return RuntimeSnapshotBuilder(
        source_registry=SourceRegistry((_Source((record,)),)),
        identity_store=_IdentityStore(links),
        mapping_store=_MappingStore(mappings),
        enrichment_store=_EnrichmentStore(),
        statement_date_provider=_StatementDates(),
    )


class RuntimeStateTests(unittest.TestCase):
    def test_builder_rehydrates_snapshot_indexes_and_amount_dependent_review(self) -> None:
        candidate = _builder().build()
        household = candidate.household
        transaction = household.transactions[0]

        self.assertEqual(len(household.transactions), 1)
        self.assertEqual(candidate.indexes.transaction_by_id[transaction.id], transaction)
        self.assertEqual(candidate.indexes.transaction_ids_by_month["2026-08"], (transaction.id,))
        self.assertEqual(candidate.indexes.transaction_ids_by_description["raw shop"], (transaction.id,))
        self.assertEqual(candidate.indexes.transaction_ids_by_merchant["Shop"], (transaction.id,))
        self.assertEqual(
            candidate.indexes.transaction_ids_by_review_signal[HIGH_VALUE_GENERAL_SHOPPING_REVIEW],
            (transaction.id,),
        )
        self.assertEqual(household.spending_payload["schema_version"], 2)
        self.assertEqual(household.financial_payload["schema_version"], 1)

    def test_projection_payloads_are_deeply_immutable_after_publication(self) -> None:
        candidate = _builder().build()
        with self.assertRaises(TypeError):
            candidate.household.spending_payload["schema_version"] = 99  # type: ignore[index]
        summary = candidate.household.spending_payload["summary"]
        self.assertIsInstance(summary, MappingProxyType)
        with self.assertRaises(TypeError):
            summary["x"] = 1  # type: ignore[index]

    def test_builder_keeps_unreconciled_evidence_pending_without_inventing_transaction_identity(self) -> None:
        candidate = _builder(linked=False).build()
        self.assertEqual(candidate.household.transactions, ())
        self.assertEqual(
            candidate.household.unreconciled_source_record_ids,
            (_record().id,),
        )
        self.assertEqual(candidate.household.statement_dates, frozenset())

    def test_runtime_store_bootstrap_and_publish_use_monotonic_generations(self) -> None:
        first = _builder().build()
        runtime = RuntimeStore()
        state1 = runtime.bootstrap(first)
        state2 = runtime.publish(first, mutation_label="test")
        self.assertEqual(state1.generation, 1)
        self.assertEqual(state2.generation, 2)
        self.assertIs(state2.household, first.household)


if __name__ == "__main__":
    unittest.main()
