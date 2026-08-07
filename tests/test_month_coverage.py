from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from family_spending.month_coverage import (
    MonthCoverageError,
    build_month_coverage,
    load_month_coverage,
    load_statement_dates,
)


class MonthCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.email_dir = self.root / "emails"
        self.email_dir.mkdir()

    def add_statement(self, value: str, digest: str = "a" * 24) -> None:
        (self.email_dir / f"{value}_{digest}.eml").write_bytes(b"test")

    def test_statement_dates_come_only_from_stable_email_filenames(self) -> None:
        self.add_statement("2026-06-10")
        self.add_statement("2026-07-10", "b" * 24)
        self.assertEqual(
            load_statement_dates(self.email_dir),
            frozenset((date(2026, 6, 10), date(2026, 7, 10))),
        )

    def test_complete_month_requires_current_and_next_month_statement(self) -> None:
        statement_dates = frozenset(
            (
                date(2025, 9, 10),
                date(2025, 10, 10),
                date(2026, 7, 10),
            )
        )
        coverage = build_month_coverage(
            ("2026-07", "2025-09", "2025-08"),
            statement_dates,
        )
        self.assertEqual(
            tuple(
                (item.month, item.is_complete, item.show)
                for item in coverage
            ),
            (
                ("2026-07", False, False),
                ("2025-09", True, True),
                ("2025-08", False, False),
            ),
        )

    def test_december_coverage_crosses_year_boundary(self) -> None:
        coverage = build_month_coverage(
            ("2025-12",),
            frozenset((date(2025, 12, 10), date(2026, 1, 10))),
        )
        self.assertTrue(coverage[0].is_complete)
        self.assertTrue(coverage[0].show)

    def test_missing_email_directory_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            MonthCoverageError,
            "Statement email directory does not exist",
        ):
            load_month_coverage(("2026-01",), self.root / "missing")

    def test_rejects_unexpected_eml_filename(self) -> None:
        (self.email_dir / "statement.eml").write_bytes(b"test")
        with self.assertRaisesRegex(MonthCoverageError, "Invalid statement email filename"):
            load_statement_dates(self.email_dir)

    def test_rejects_invalid_statistics_month(self) -> None:
        with self.assertRaisesRegex(MonthCoverageError, "expected YYYY-MM"):
            build_month_coverage(("2026-13",), frozenset())


if __name__ == "__main__":
    unittest.main()
