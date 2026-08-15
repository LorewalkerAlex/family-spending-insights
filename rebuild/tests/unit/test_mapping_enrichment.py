from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.domain.enrichment import (
    INCOME_DEFAULT_CATEGORY,
    OTHER_EXPENSE_REVIEW,
    EnrichmentDecision,
    resolve_enrichment,
)
from family_spending.domain.errors import DomainInvariantError
from family_spending.domain.mapping import MappingCatalog, UNCLASSIFIED_CATEGORY
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import transaction_from_source_record


class MappingEnrichmentTests(unittest.TestCase):
    def mappings(self, category: str = "food") -> MappingCatalog:
        return MappingCatalog(
            description_to_merchant={"raw merchant": "Merchant"},
            merchant_to_category={"Merchant": category},
            categories=frozenset({category}),
        )

    def source(self, transaction_type: str = "expense", description: str = "raw merchant") -> SourceRecord:
        return SourceRecord(
            identity=SourceIdentity("manual", "manual-1", "record"),
            transaction_type=transaction_type,  # type: ignore[arg-type]
            transaction_date=date(2026, 8, 2),
            amount=Decimal("88.00"),
            currency="CNY",
            description=description,
        )

    def test_mapping_catalog_rejects_orphaned_merchants_and_runtime_category(self) -> None:
        with self.assertRaisesRegex(DomainInvariantError, "merchant sets"):
            MappingCatalog(
                description_to_merchant={"raw": "Merchant"},
                merchant_to_category={},
                categories=frozenset(),
            )
        with self.assertRaisesRegex(DomainInvariantError, "runtime state"):
            MappingCatalog(
                description_to_merchant={"raw": "Merchant"},
                merchant_to_category={"Merchant": UNCLASSIFIED_CATEGORY},
                categories=frozenset({UNCLASSIFIED_CATEGORY}),
            )

    def test_mapping_change_propagates_when_transaction_has_no_override(self) -> None:
        source = self.source()
        transaction = transaction_from_source_record(source)
        first = resolve_enrichment(transaction, source, self.mappings("food"))
        second = resolve_enrichment(transaction, source, self.mappings("daily"))

        self.assertEqual(first.category, "food")
        self.assertEqual(second.category, "daily")
        self.assertEqual(second.category_source, "merchant_default")

    def test_sparse_overrides_and_note_survive_mapping_change(self) -> None:
        source = self.source()
        transaction = transaction_from_source_record(source)
        decision = EnrichmentDecision(
            transaction_id=transaction.id,
            merchant_override="Merchant",
            category_override="special",
            note="remember this",
        )
        before = MappingCatalog(
            {"raw merchant": "Merchant", "other": "Other"},
            {"Merchant": "food", "Other": "special"},
            frozenset({"food", "special"}),
        )
        after = MappingCatalog(
            {"raw merchant": "Merchant", "other": "Other"},
            {"Merchant": "daily", "Other": "special"},
            frozenset({"daily", "special"}),
        )

        first = resolve_enrichment(transaction, source, before, decision)
        second = resolve_enrichment(transaction, source, after, decision)
        self.assertEqual(first.category, "special")
        self.assertEqual(second.category, "special")
        self.assertEqual(second.category_source, "transaction_override")
        self.assertEqual(second.note, "remember this")

    def test_income_bypasses_expense_mapping_and_allows_note_only(self) -> None:
        source = self.source(transaction_type="income")
        transaction = transaction_from_source_record(source)
        decision = EnrichmentDecision(transaction_id=transaction.id, note="salary note")
        resolved = resolve_enrichment(transaction, source, self.mappings(), decision)

        self.assertIsNone(resolved.merchant_name)
        self.assertEqual(resolved.category, INCOME_DEFAULT_CATEGORY)
        self.assertEqual(resolved.category_source, "income_default")
        self.assertEqual(resolved.note, "salary note")

        with self.assertRaisesRegex(DomainInvariantError, "Note decisions only"):
            resolve_enrichment(
                transaction,
                source,
                self.mappings(),
                EnrichmentDecision(transaction_id=transaction.id, merchant_override="Merchant"),
            )

    def test_unmatched_expense_remains_visible_and_unclassified(self) -> None:
        source = self.source(description="unknown")
        transaction = transaction_from_source_record(source)
        resolved = resolve_enrichment(transaction, source, self.mappings())

        self.assertIsNone(resolved.merchant_name)
        self.assertEqual(resolved.display_name, "unknown")
        self.assertEqual(resolved.category, UNCLASSIFIED_CATEGORY)
        self.assertTrue(resolved.is_unclassified)

    def test_other_expense_mapping_keeps_review_signal(self) -> None:
        source = self.source()
        transaction = transaction_from_source_record(source)
        resolved = resolve_enrichment(
            transaction,
            source,
            self.mappings("\u5176\u4ed6\u652f\u51fa"),
        )
        self.assertEqual(resolved.review_signals, (OTHER_EXPENSE_REVIEW,))


if __name__ == "__main__":
    unittest.main()
