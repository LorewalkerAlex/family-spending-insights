from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.sources.manual.model import (
    ManualEvidence,
    create_manual_evidence,
    manual_evidence_to_source_record,
)
from family_spending.sources.manual.source import ManualSource


class _Reader:
    def __init__(self, records: tuple[ManualEvidence, ...]) -> None:
        self._records = records

    def load_all(self) -> tuple[ManualEvidence, ...]:
        return self._records


class ManualSourceTests(unittest.TestCase):
    def test_creation_normalizes_source_native_fields(self) -> None:
        evidence = create_manual_evidence(
            transaction_type="expense",
            transaction_date=date(2026, 8, 15),
            amount=Decimal("18.50"),
            description="  breakfast stand  ",
            currency=" cny ",
            evidence_id="manual_breakfast",
        )
        self.assertEqual(evidence.description, "breakfast stand")
        self.assertEqual(evidence.currency, "CNY")
        self.assertEqual(evidence.evidence_id, "manual_breakfast")

    def test_explicit_correction_keeps_source_identity(self) -> None:
        original = create_manual_evidence(
            transaction_type="expense",
            transaction_date=date(2026, 8, 15),
            amount=Decimal("18.50"),
            description="breakfast",
            evidence_id="manual_breakfast",
        )
        corrected = create_manual_evidence(
            transaction_type="expense",
            transaction_date=date(2026, 8, 15),
            amount=Decimal("20.00"),
            description="corrected breakfast",
            evidence_id=original.evidence_id,
        )
        original_record = manual_evidence_to_source_record(original)
        corrected_record = manual_evidence_to_source_record(corrected)
        self.assertEqual(original_record.id, corrected_record.id)
        self.assertNotEqual(original_record.amount, corrected_record.amount)
        self.assertEqual(original_record.identity.evidence_identity, "manual_breakfast")
        self.assertEqual(original_record.identity.record_locator, "record")

    def test_source_adapts_every_manual_evidence_record(self) -> None:
        first = create_manual_evidence(
            transaction_type="income",
            transaction_date=date(2026, 8, 1),
            amount=Decimal("100.00"),
            description="salary adjustment",
            evidence_id="manual_income",
        )
        second = create_manual_evidence(
            transaction_type="expense",
            transaction_date=date(2026, 8, 2),
            amount=Decimal("9.00"),
            description=None,
            evidence_id="manual_expense",
        )
        records = ManualSource(_Reader((first, second))).load_records()
        self.assertEqual(tuple(record.source_type for record in records), ("manual", "manual"))
        self.assertEqual(tuple(record.transaction_type for record in records), ("income", "expense"))


if __name__ == "__main__":
    unittest.main()
