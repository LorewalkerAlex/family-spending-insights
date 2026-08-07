from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.month_coverage import MonthCoverage
from family_spending.spending_statistics import (
    CategoryStatistics,
    MerchantStatistics,
    MonthlySpendingStatistics,
    SpendingStatistics,
)
from family_spending.statistics_serialization import (
    StatisticsSerializationError,
    encode_spending_statistics,
    serialize_spending_statistics,
    write_spending_statistics_json,
)


def make_month(
    month: str,
    total: str,
    *,
    transaction_count: int = 1,
) -> MonthlySpendingStatistics:
    spending = Decimal(total)
    return MonthlySpendingStatistics(
        month=month,
        total_spending=spending,
        transaction_count=transaction_count,
        categories=(
            CategoryStatistics(
                category="餐饮美食",
                spending=spending,
                transaction_count=transaction_count,
            ),
        ),
        merchants=(
            MerchantStatistics(
                merchant_name="测试商户",
                display_name="测试商户",
                is_unclassified=False,
                spending=spending,
                transaction_count=transaction_count,
            ),
        ),
    )


def make_statistics(*, june_total: str = "1234.56") -> SpendingStatistics:
    june = make_month("2026-06", june_total, transaction_count=2)
    july = make_month("2026-07", "50.00")
    return SpendingStatistics(
        total_spending=june.total_spending + july.total_spending,
        transaction_count=3,
        months=(july, june),
    )


def make_coverage() -> tuple[MonthCoverage, ...]:
    return (
        MonthCoverage(month="2026-07", is_complete=False, show=False),
        MonthCoverage(month="2026-06", is_complete=True, show=True),
    )


class StatisticsSerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_serializes_schema_v2_with_all_and_shown_summaries(self) -> None:
        payload = serialize_spending_statistics(make_statistics(), make_coverage())
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(
            payload["summary"]["all_data"],
            {
                "total_spending_minor": 128456,
                "transaction_count": 3,
                "month_count": 2,
            },
        )
        self.assertEqual(
            payload["summary"]["shown_data"],
            {
                "total_spending_minor": 123456,
                "transaction_count": 2,
                "month_count": 1,
            },
        )
        july, june = payload["months"]
        self.assertFalse(july["is_complete"])
        self.assertFalse(july["show"])
        self.assertTrue(june["is_complete"])
        self.assertTrue(june["show"])
        self.assertEqual(june["categories"][0]["spending_minor"], 123456)
        self.assertEqual(june["merchants"][0]["merchant_name"], "测试商户")

    def test_show_is_independent_from_is_complete_in_public_schema(self) -> None:
        coverage = (
            MonthCoverage(month="2026-07", is_complete=False, show=True),
            MonthCoverage(month="2026-06", is_complete=True, show=False),
        )
        payload = serialize_spending_statistics(make_statistics(), coverage)
        self.assertEqual(payload["summary"]["shown_data"]["total_spending_minor"], 5000)
        self.assertTrue(payload["months"][0]["show"])
        self.assertFalse(payload["months"][1]["show"])

    def test_rejects_month_coverage_mismatch(self) -> None:
        with self.assertRaisesRegex(
            StatisticsSerializationError,
            "Month coverage does not match statistics months",
        ):
            serialize_spending_statistics(
                make_statistics(),
                (MonthCoverage("2026-06", True, True),),
            )

    def test_rejects_more_than_two_decimal_places(self) -> None:
        with self.assertRaisesRegex(
            StatisticsSerializationError,
            "more than two decimal places",
        ):
            serialize_spending_statistics(
                make_statistics(june_total="1.001"),
                make_coverage(),
            )

    def test_encoding_is_utf8_friendly_and_deterministic(self) -> None:
        payload = serialize_spending_statistics(make_statistics(), make_coverage())
        first = encode_spending_statistics(payload)
        second = encode_spending_statistics(payload)
        self.assertEqual(first, second)
        self.assertIn("餐饮美食", first)
        self.assertTrue(first.endswith("\n"))

    def test_atomic_write_replaces_file_and_preserves_utf8(self) -> None:
        output_path = self.root / "reports" / "spending_statistics.json"
        payload = serialize_spending_statistics(make_statistics(), make_coverage())
        write_spending_statistics_json(payload, output_path)
        loaded = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded, payload)
        self.assertEqual(
            tuple(output_path.parent.glob(f".{output_path.name}.*.tmp")),
            (),
        )

    def test_replace_failure_preserves_old_file_and_cleans_temp_file(self) -> None:
        output_path = self.root / "spending_statistics.json"
        output_path.write_text("old content", encoding="utf-8")
        payload = serialize_spending_statistics(make_statistics(), make_coverage())
        with patch(
            "family_spending.statistics_serialization.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(OSError, "replace failed"):
                write_spending_statistics_json(payload, output_path)
        self.assertEqual(output_path.read_text(encoding="utf-8"), "old content")
        self.assertEqual(
            tuple(output_path.parent.glob(f".{output_path.name}.*.tmp")),
            (),
        )


if __name__ == "__main__":
    unittest.main()
