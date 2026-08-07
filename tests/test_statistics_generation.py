from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    write_transactions_csv,
)
from family_spending.mapping import MappingResolutionError
from family_spending.statistics_generation import (
    format_statistics_generation_report,
    generate_spending_statistics,
    main,
)
from family_spending.transaction_resolution import TransactionResolutionError

MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
测试家电:
  - 支付宝-测试家电
  - 退款-支付宝-测试家电
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
家居家电:
  - 测试家电
"""


def transaction(
    transaction_id: str,
    amount: str,
    *,
    transaction_date: date,
    description: str,
    source_index: int,
) -> CmbTransaction:
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=transaction_date,
        amount=Decimal(amount),
        description=description,
        source_email="statement.eml",
        source_index=source_index,
    )


class StatisticsGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.transactions_path = self.root / "transactions.csv"
        self.merchants_path = self.root / "merchants.yaml"
        self.categories_path = self.root / "categories.yaml"
        self.overrides_path = self.root / "transaction_category_overrides.jsonl"
        self.output_path = self.root / "reports" / "spending_statistics.json"
        self.emails_dir = self.root / "emails"
        self.emails_dir.mkdir()
        self.merchants_path.write_text(MERCHANTS, encoding="utf-8")
        self.categories_path.write_text(CATEGORIES, encoding="utf-8")
        for index, statement_date in enumerate(
            ("2025-12-10", "2026-01-10", "2026-02-10"),
            start=1,
        ):
            digest = format(index, "024x")
            (self.emails_dir / f"{statement_date}_{digest}.eml").write_bytes(b"test")

    def generate(self):
        return generate_spending_statistics(
            self.transactions_path,
            self.merchants_path,
            self.categories_path,
            self.overrides_path,
            self.output_path,
            self.emails_dir,
        )

    def test_full_pipeline_reconciles_refunds_maps_and_writes_statistics(self) -> None:
        transactions = (
            transaction(
                "cmb_partial_override",
                "3000",
                transaction_date=date(2025, 12, 1),
                description="支付宝-测试家电",
                source_index=1,
            ),
            transaction(
                "cmb_partial_refund",
                "-1000",
                transaction_date=date(2026, 1, 1),
                description="支付宝-测试家电",
                source_index=2,
            ),
            transaction(
                "cmb_food",
                "20",
                transaction_date=date(2026, 1, 2),
                description="支付宝-测试餐饮",
                source_index=3,
            ),
            transaction(
                "cmb_unknown",
                "30",
                transaction_date=date(2026, 1, 3),
                description="支付宝-未知商户",
                source_index=4,
            ),
        )
        write_transactions_csv(transactions, self.transactions_path)
        self.overrides_path.write_text(
            '{"transaction_id":"cmb_partial_override","category":"餐饮美食","note":"测试覆盖"}\n',
            encoding="utf-8",
        )
        summary = self.generate()
        payload = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(summary.raw_transactions, 4)
        self.assertEqual(summary.refund_transactions, 1)
        self.assertEqual(summary.net_consumption_transactions, 3)
        self.assertEqual(summary.partially_refunded_transactions, 1)
        self.assertEqual(summary.unclassified_net_transactions, 1)
        self.assertEqual(summary.total_net_spending, Decimal("2050"))
        self.assertEqual(summary.shown_months, 2)
        self.assertEqual(summary.shown_net_spending, Decimal("2050"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(
            payload["summary"]["all_data"]["total_spending_minor"],
            205000,
        )
        self.assertEqual(
            payload["summary"]["shown_data"]["total_spending_minor"],
            205000,
        )
        self.assertEqual(
            tuple(month["month"] for month in payload["months"]),
            ("2026-01", "2025-12"),
        )
        self.assertTrue(all(month["is_complete"] for month in payload["months"]))
        self.assertTrue(all(month["show"] for month in payload["months"]))
        december = payload["months"][1]
        self.assertEqual(december["total_spending_minor"], 200000)
        self.assertEqual(december["categories"][0]["category"], "餐饮美食")
        january = payload["months"][0]
        self.assertEqual(january["total_spending_minor"], 5000)
        self.assertEqual(
            {item["category"] for item in january["categories"]},
            {"餐饮美食", "待分类"},
        )
        unknown = next(
            item
            for item in january["merchants"]
            if item["is_unclassified"]
        )
        self.assertIsNone(unknown["merchant_name"])
        self.assertEqual(unknown["display_name"], "支付宝-未知商户")

    def test_incomplete_month_stays_in_all_data_but_not_shown_data(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_march",
                    "20",
                    transaction_date=date(2026, 3, 2),
                    description="支付宝-测试餐饮",
                    source_index=1,
                ),
            ),
            self.transactions_path,
        )
        self.overrides_path.write_text("", encoding="utf-8")
        summary = self.generate()
        payload = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(summary.months, 1)
        self.assertEqual(summary.total_net_spending, Decimal("20"))
        self.assertEqual(summary.shown_months, 0)
        self.assertEqual(summary.shown_net_spending, Decimal("0"))
        self.assertEqual(payload["summary"]["all_data"]["total_spending_minor"], 2000)
        self.assertEqual(payload["summary"]["shown_data"]["total_spending_minor"], 0)
        self.assertFalse(payload["months"][0]["is_complete"])
        self.assertFalse(payload["months"][0]["show"])

    def test_pipeline_uses_same_merchant_refund_fallback(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_purchase",
                    "100",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-测试家电",
                    source_index=1,
                ),
                transaction(
                    "cmb_refund",
                    "-100",
                    transaction_date=date(2026, 1, 20),
                    description="退款-支付宝-测试家电",
                    source_index=2,
                ),
            ),
            self.transactions_path,
        )
        self.overrides_path.write_text("", encoding="utf-8")
        summary = self.generate()
        payload = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(summary.same_merchant_refund_matches, 1)
        self.assertEqual(summary.same_merchant_matched_amount, Decimal("100"))
        self.assertEqual(summary.net_consumption_transactions, 0)
        self.assertEqual(summary.unmatched_refund_count, 0)
        self.assertEqual(summary.shown_months, 0)
        self.assertEqual(payload["months"], [])
        report = format_statistics_generation_report(summary)
        self.assertIn("Same-merchant refund matches: 1", report)
        self.assertIn("Same-merchant matched amount: 100", report)

    def test_zero_amount_transaction_is_ignored_and_summarized(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_zero",
                    "0",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-测试餐饮",
                    source_index=1,
                ),
                transaction(
                    "cmb_food",
                    "20",
                    transaction_date=date(2026, 1, 2),
                    description="支付宝-测试餐饮",
                    source_index=2,
                ),
            ),
            self.transactions_path,
        )
        self.overrides_path.write_text("", encoding="utf-8")
        summary = self.generate()
        payload = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(summary.raw_transactions, 2)
        self.assertEqual(summary.zero_amount_transactions, 1)
        self.assertEqual(summary.net_consumption_transactions, 1)
        self.assertEqual(summary.total_net_spending, Decimal("20"))
        self.assertEqual(payload["summary"]["all_data"]["transaction_count"], 1)
        self.assertEqual(payload["summary"]["shown_data"]["transaction_count"], 1)
        self.assertEqual(payload["summary"]["shown_data"]["total_spending_minor"], 2000)
        self.assertIn(
            "Zero-amount transactions ignored: 1",
            format_statistics_generation_report(summary),
        )

    def test_fully_refunded_override_is_valid_but_absent_from_statistics(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_override",
                    "100",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-测试家电",
                    source_index=1,
                ),
                transaction(
                    "cmb_refund",
                    "-100",
                    transaction_date=date(2026, 1, 2),
                    description="支付宝-测试家电",
                    source_index=2,
                ),
            ),
            self.transactions_path,
        )
        self.overrides_path.write_text(
            '{"transaction_id":"cmb_override","category":"餐饮美食"}\n',
            encoding="utf-8",
        )
        summary = self.generate()
        payload = json.loads(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual(summary.fully_refunded_transactions, 1)
        self.assertEqual(summary.net_consumption_transactions, 0)
        self.assertEqual(payload["months"], [])

    def test_missing_override_id_still_fails_against_raw_transactions(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_food",
                    "20",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-测试餐饮",
                    source_index=1,
                ),
            ),
            self.transactions_path,
        )
        self.overrides_path.write_text(
            '{"transaction_id":"cmb_missing","category":"餐饮美食"}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            TransactionResolutionError,
            "cmb_missing",
        ):
            self.generate()
        self.assertFalse(self.output_path.exists())

    def test_unmapped_override_transaction_still_fails(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_override",
                    "20",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-未知商户",
                    source_index=1,
                ),
            ),
            self.transactions_path,
        )
        self.overrides_path.write_text(
            '{"transaction_id":"cmb_override","category":"餐饮美食"}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            MappingResolutionError,
            "description is not mapped",
        ):
            self.generate()
        self.assertFalse(self.output_path.exists())

    def test_report_contains_all_and_shown_aggregates_without_transaction_details(self) -> None:
        write_transactions_csv(
            (
                transaction(
                    "cmb_food",
                    "20",
                    transaction_date=date(2026, 1, 1),
                    description="支付宝-测试餐饮",
                    source_index=1,
                ),
            ),
            self.transactions_path,
        )
        self.overrides_path.write_text("", encoding="utf-8")
        report = format_statistics_generation_report(self.generate())
        self.assertIn("Raw transactions: 1", report)
        self.assertIn("Total net spending: 20", report)
        self.assertIn("Shown months: 1", report)
        self.assertIn("Shown net spending: 20", report)
        self.assertNotIn("cmb_food", report)
        self.assertNotIn("支付宝-测试餐饮", report)

    def test_main_converts_known_errors_to_clean_cli_failure(self) -> None:
        with patch(
            "family_spending.statistics_generation.generate_spending_statistics",
            side_effect=TransactionResolutionError("bad override"),
        ):
            with self.assertRaisesRegex(
                SystemExit,
                "Spending statistics generation failed.*bad override",
            ):
                main()


if __name__ == "__main__":
    unittest.main()
