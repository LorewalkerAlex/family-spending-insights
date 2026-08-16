from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.application.source_registry import SourceRegistry, SourceRegistryError
from family_spending.domain.source import SourceIdentity, SourceRecord


def record(source_type: str, evidence: str) -> SourceRecord:
    return SourceRecord(
        identity=SourceIdentity(
            source_type=source_type,
            evidence_identity=evidence,
            record_locator="record",
        ),
        transaction_type="expense",
        transaction_date=date(2026, 8, 15),
        amount=Decimal("1.00"),
        currency="CNY",
        description="test",
    )


class FakeSource:
    def __init__(self, source_type: str, records: tuple[SourceRecord, ...]) -> None:
        self.source_type = source_type
        self._records = records

    def load_records(self) -> tuple[SourceRecord, ...]:
        return self._records


class SourceRegistryTests(unittest.TestCase):
    def test_fake_source_plugs_in_without_registry_branching(self) -> None:
        fake = FakeSource("future_source", (record("future_source", "future-1"),))
        registry = SourceRegistry((fake,))
        loaded = registry.load_records()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].source_type, "future_source")

    def test_duplicate_registered_source_type_fails(self) -> None:
        first = FakeSource("manual", ())
        second = FakeSource("manual", ())
        with self.assertRaisesRegex(SourceRegistryError, "Duplicate registered Source type"):
            SourceRegistry((first, second))

    def test_source_returning_wrong_type_fails(self) -> None:
        bad = FakeSource("manual", (record("cmb_email", "email-1"),))
        with self.assertRaisesRegex(SourceRegistryError, "with source_type"):
            SourceRegistry((bad,)).load_records()


if __name__ == "__main__":
    unittest.main()
