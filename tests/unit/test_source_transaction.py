from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import SourceLink, build_transaction_id, transaction_from_source_record


class SourceTransactionTests(unittest.TestCase):
    def source(self, locator: str = "fingerprint:entry-a") -> SourceRecord:
        return SourceRecord(
            identity=SourceIdentity(
                source_type="cmb_email",
                evidence_identity="sha256:statement",
                record_locator=locator,
            ),
            transaction_type="expense",
            transaction_date=date(2026, 8, 1),
            amount=Decimal("12.34"),
            currency="CNY",
            description="merchant text",
        )

    def test_source_identity_is_evidence_anchored_and_deterministic(self) -> None:
        first = self.source()
        repeated = self.source()
        another_record = self.source("fingerprint:entry-b")

        self.assertEqual(first.id, repeated.id)
        self.assertNotEqual(first.id, another_record.id)
        self.assertTrue(first.id.startswith("src_"))

    def test_source_identity_rejects_empty_stable_locator(self) -> None:
        with self.assertRaisesRegex(DomainInvariantError, "record_locator"):
            SourceIdentity(
                source_type="cmb_email",
                evidence_identity="sha256:statement",
                record_locator="",
            )

    def test_transaction_id_stays_stable_and_authority_rebuild_can_preserve_it(self) -> None:
        source = self.source()
        transaction_id = build_transaction_id(source)
        created = transaction_from_source_record(source)
        rebuilt = transaction_from_source_record(source, transaction_id=transaction_id)

        self.assertEqual(created.id, transaction_id)
        self.assertEqual(rebuilt.id, transaction_id)
        self.assertEqual(rebuilt.amount, source.amount)

    def test_source_link_rejects_invalid_roles(self) -> None:
        with self.assertRaisesRegex(DomainInvariantError, "role"):
            SourceLink(
                transaction_id="txn_1",
                source_record_id="src_1",
                role="primary",  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
