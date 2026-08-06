from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

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


def make_statistics(*, total: str = "1234.56") -> SpendingStatistics:
    spending = Decimal(total)
    month = MonthlySpendingStatistics(
        month="2026-06",
        total_spending=spending,
        transaction_count=2,
        categories=(
            CategoryStatistics(
                category="餐饮美食",
                spending=spending,
                transaction_count=2,
            ),
        ),
        merchants=(
            MerchantStatistics(
                merchant_name="测试商户",
                display_name="测试商户",
                is_unclassified=False,
                spending=spending,
                transaction_count=2,
            ),
        ),
    )
    return SpendingStatistics(
        total_spending=spending,
        transaction_count=2,
        months=(month,),
    )


class StatisticsSerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_serializes_public_schema_with_minor_units(self) -> None:
        payload = serialize_spending_statistics(make_statistics())
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["summary"]["total_spending_minor"], 123456)
        self.assertEqual(payload["summary"]["month_count"], 1)
        month = payload["months"][0]
        self.assertEqual(month["month"], "2026-06")
        self.assertEqual(month["categories"][0]["spending_minor"], 123456)
        self.assertEqual(month["merchants"][0]["merchant_name"], "测试商户")

    def test_rejects_more_than_two_decimal_places(self) -> None:
        with self.assertRaisesRegex(
            StatisticsSerializationError,
            "more than two decimal places",
        ):
            serialize_spending_statistics(make_statistics(total="1.001"))

    def test_encoding_is_utf8_friendly_and_deterministic(self) -> None:
        payload = serialize_spending_statistics(make_statistics())
        first = encode_spending_statistics(payload)
        second = encode_spending_statistics(payload)
        self.assertEqual(first, second)
        self.assertIn("餐饮美食", first)
        self.assertTrue(first.endswith("\n"))

    def test_atomic_write_replaces_file_and_preserves_utf8(self) -> None:
        output_path = self.root / "reports" / "spending_statistics.json"
        payload = serialize_spending_statistics(make_statistics())
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
        payload = serialize_spending_statistics(make_statistics())
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
