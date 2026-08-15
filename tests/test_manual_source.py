from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from family_spending.manual_source import (
    ManualSourceAdapter,
    ManualSourceDataError,
    create_manual_source_entry,
    read_manual_source_entries,
    write_manual_source_entries,
)


class ManualSourceTests(unittest.TestCase):
    def test_source_native_description_round_trips_without_forced_mapping_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manual_source_records.jsonl"
            entry = create_manual_source_entry(
                transaction_type="expense",
                transaction_date=date(2026, 8, 15),
                amount=Decimal("18.50"),
                description="  小区早餐摊  ",
                note="现金",
                source_record_id="manual_breakfast",
            )
            write_manual_source_entries((entry,), path)
            loaded = read_manual_source_entries(path)

            self.assertEqual(loaded, (entry,))
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["description"], "小区早餐摊")
            self.assertNotIn("merchant", persisted)
            self.assertNotIn("category", persisted)

            record = ManualSourceAdapter().adapt(entry)
            self.assertEqual(record.id, "manual_breakfast")
            self.assertEqual(record.description, "小区早餐摊")
            self.assertEqual(record.amount, Decimal("18.50"))

    def test_missing_and_empty_source_are_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manual_source_records.jsonl"
            self.assertEqual(read_manual_source_entries(path), ())
            write_manual_source_entries((), path)
            self.assertFalse(path.exists())

    def test_invalid_persisted_record_fails_instead_of_silent_repair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manual_source_records.jsonl"
            path.write_text(
                '{"id":"manual_bad","type":"expense","date":"bad","amount":"1"}\n',
                encoding="utf-8",
            )
            with self.assertRaises(ManualSourceDataError):
                read_manual_source_entries(path)


if __name__ == "__main__":
    unittest.main()
