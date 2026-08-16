from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from family_spending.domain.enrichment import resolve_enrichment
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import transaction_from_source_record
from family_spending.projections.financial import (
    FinancialProjectionError,
    build_financial_projection,
)
from family_spending.projections.month_coverage import build_month_coverage
from family_spending.projections.spending import build_spending_projection


class ProjectionTests(unittest.TestCase):
    def source(
        self,
        key: str,
        when: str,
        amount: str,
        description: str,
        *,
        transaction_type: str = "expense",
    ) -> SourceRecord:
        return SourceRecord(
            SourceIdentity("manual", key, "record"),
            transaction_type,  # type: ignore[arg-type]
            date.fromisoformat(when),
            Decimal(amount),
            "CNY",
            description,
        )

    def build_state(self, sources: tuple[SourceRecord, ...], mappings: MappingCatalog):
        transactions = tuple(transaction_from_source_record(source) for source in sources)
        source_by_transaction = {
            transaction.id: source
            for transaction, source in zip(transactions, sources, strict=True)
        }
        enrichments = {
            transaction.id: resolve_enrichment(
                transaction,
                source_by_transaction[transaction.id],
                mappings,
            )
            for transaction in transactions
        }
        return transactions, source_by_transaction, enrichments

    def test_month_coverage_uses_statement_dates_not_evidence_filenames(self) -> None:
        coverage = build_month_coverage(
            ("2026-01", "2026-02"),
            frozenset({date(2026, 1, 10), date(2026, 2, 10)}),
        )
        self.assertTrue(coverage[0].is_complete)
        self.assertFalse(coverage[1].is_complete)
        self.assertEqual(tuple(item.show for item in coverage), (True, False))

    def test_spending_and_financial_projection_preserve_current_schema_semantics(self) -> None:
        mappings = MappingCatalog(
            {
                "appliance": "Appliance",
                "food": "Food",
                "unknown-refund": "Appliance",
            },
            {"Appliance": "家居家电", "Food": "餐饮美食"},
            frozenset({"家居家电", "餐饮美食"}),
        )
        sources = (
            self.source("purchase", "2025-12-01", "3000", "appliance"),
            self.source("refund", "2026-01-01", "-1000", "appliance"),
            self.source("food", "2026-01-02", "20", "food"),
            self.source("unknown", "2026-01-03", "30", "unmapped"),
            self.source("income", "2026-01-05", "1000", "salary", transaction_type="income"),
        )
        transactions, source_by_transaction, enrichments = self.build_state(sources, mappings)
        statement_dates = frozenset(
            {date(2025, 12, 10), date(2026, 1, 10), date(2026, 2, 10)}
        )

        spending = build_spending_projection(
            transactions,
            source_by_transaction,
            enrichments,
            statement_dates,
        )
        financial = build_financial_projection(
            transactions,
            spending.statistics,
            statement_dates,
        )

        self.assertEqual(spending.payload["schema_version"], 2)
        self.assertEqual(spending.summary.total_net_spending, Decimal("2050"))
        self.assertEqual(spending.summary.net_consumption_transactions, 3)
        self.assertEqual(spending.summary.unclassified_net_transactions, 1)
        self.assertEqual(spending.summary.partially_refunded_transactions, 1)
        self.assertEqual(spending.payload["summary"]["all_data"]["total_spending_minor"], 205000)  # type: ignore[index]
        self.assertEqual(spending.payload["summary"]["shown_data"]["total_spending_minor"], 205000)  # type: ignore[index]

        self.assertEqual(financial.payload["schema_version"], 1)
        self.assertEqual(financial.payload["summary"]["all_data"]["total_income_minor"], 100000)  # type: ignore[index]
        self.assertEqual(financial.payload["summary"]["all_data"]["total_spending_minor"], 205000)  # type: ignore[index]
        self.assertEqual(financial.payload["summary"]["all_data"]["net_cash_flow_minor"], -105000)  # type: ignore[index]

    def test_income_only_month_is_present_and_requires_positive_income(self) -> None:
        mappings = MappingCatalog.empty()
        sources = (
            self.source("income", "2026-01-05", "1000", "salary", transaction_type="income"),
        )
        transactions, source_by_transaction, enrichments = self.build_state(sources, mappings)
        statement_dates = frozenset({date(2026, 1, 10), date(2026, 2, 10)})
        spending = build_spending_projection(
            transactions,
            source_by_transaction,
            enrichments,
            statement_dates,
        )
        financial = build_financial_projection(transactions, spending.statistics, statement_dates)
        self.assertEqual(spending.statistics.months, ())
        self.assertEqual(financial.payload["months"][0]["month"], "2026-01")  # type: ignore[index]
        self.assertEqual(financial.payload["summary"]["shown_data"]["net_cash_flow_minor"], 100000)  # type: ignore[index]

        bad = (
            transaction_from_source_record(
                self.source("bad-income", "2026-01-05", "0", "salary", transaction_type="income")
            ),
        )
        with self.assertRaisesRegex(FinancialProjectionError, "positive amount"):
            build_financial_projection(bad, spending.statistics, statement_dates)


if __name__ == "__main__":
    unittest.main()
