from __future__ import annotations

import csv
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from loguru import logger

from family_spending.cleaning import cmb_transactions
from family_spending.cleaning.cmb_transactions import (
    INPUT_FIELDS,
    OUTPUT_FIELDS,
    CmbCleaningBatchResult,
    CmbStatementContractError,
    build_transaction_id,
    complete_mmdd,
    load_config,
    process_cmb_transactions,
    read_cmb_statement_csv,
    write_cleaned_transactions_csv,
)

logger.remove()


def statement_row(**overrides: str) -> dict[str, str]:
    row = {
        "source_email": "sample.eml",
        "email_date": "2026-07-10",
        "transaction_index": "1",
        "transaction_mmdd": "0701",
        "post_mmdd": "0702",
        "description": "Example Merchant",
        "raw_amount_text": "USD 12.30",
        "raw_amount": "12.30",
        "card_last4": "0000",
        "country_or_region": "US",
        "cny_amount_text": "CNY 88.88",
        "cny_amount": "88.88",
    }
    row.update(overrides)
    return row


def write_statement(
    path: Path,
    rows: list[dict[str, str]],
    *,
    fields: tuple[str, ...] = INPUT_FIELDS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_output(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or ()), list(reader)


class CmbTransactionInputContractTests(unittest.TestCase):
    def test_normal_statement_is_read_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row()])

            transactions = read_cmb_statement_csv(path)

        self.assertEqual(len(transactions), 1)
        transaction = transactions[0]
        self.assertEqual(transaction.source, "cmb")
        self.assertEqual(transaction.source_email, "sample.eml")
        self.assertEqual(transaction.source_transaction_index, 1)
        self.assertEqual(transaction.bank_description, "Example Merchant")

    def test_missing_required_field_fails_file(self) -> None:
        fields = tuple(field for field in INPUT_FIELDS if field != "description")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row()], fields=fields)

            with self.assertRaisesRegex(CmbStatementContractError, "missing required"):
                read_cmb_statement_csv(path)

    def test_source_email_must_be_nonempty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(source_email="")])

            with self.assertRaisesRegex(CmbStatementContractError, "empty source_email"):
                read_cmb_statement_csv(path)

    def test_source_email_must_be_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(
                path,
                [
                    statement_row(),
                    statement_row(source_email="other.eml", transaction_index="2"),
                ],
            )

            with self.assertRaisesRegex(CmbStatementContractError, "inconsistent"):
                read_cmb_statement_csv(path)

    def test_source_email_stem_must_match_csv_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(source_email="other.eml")])

            with self.assertRaisesRegex(CmbStatementContractError, "does not match"):
                read_cmb_statement_csv(path)

    def test_email_date_must_be_valid_and_consistent(self) -> None:
        cases = (
            ([statement_row(email_date="2026-02-30")], "calendar"),
            ([statement_row(email_date="2026-7-10")], "format"),
            (
                [statement_row(), statement_row(email_date="2026-07-09", transaction_index="2")],
                "inconsistent",
            ),
        )
        for rows, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "sample.csv"
                write_statement(path, rows)
                with self.assertRaisesRegex(CmbStatementContractError, message):
                    read_cmb_statement_csv(path)

    def test_transaction_index_must_be_positive_integer(self) -> None:
        for value in ("0", "-1", "1.0", "01", ""):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "sample.csv"
                write_statement(path, [statement_row(transaction_index=value)])
                with self.assertRaisesRegex(CmbStatementContractError, "invalid transaction_index"):
                    read_cmb_statement_csv(path)

    def test_duplicate_transaction_index_fails_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(), statement_row()])

            with self.assertRaisesRegex(CmbStatementContractError, "duplicate"):
                read_cmb_statement_csv(path)

    def test_transaction_index_must_be_consecutive_in_input_order(self) -> None:
        cases = (
            [statement_row(transaction_index="2")],
            [statement_row(), statement_row(transaction_index="3")],
        )
        for rows in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "sample.csv"
                write_statement(path, rows)
                with self.assertRaisesRegex(CmbStatementContractError, "consecutive"):
                    read_cmb_statement_csv(path)

    def test_invalid_amount_fails_file(self) -> None:
        for field in ("raw_amount", "cny_amount"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "sample.csv"
                write_statement(path, [statement_row(**{field: "12,30"})])
                with self.assertRaisesRegex(CmbStatementContractError, f"invalid {field}"):
                    read_cmb_statement_csv(path)

    def test_at_least_one_numeric_amount_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(raw_amount="", cny_amount="")])

            with self.assertRaisesRegex(CmbStatementContractError, "missing numeric"):
                read_cmb_statement_csv(path)

    def test_empty_description_fails_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(description="  ")])

            with self.assertRaisesRegex(CmbStatementContractError, "empty description"):
                read_cmb_statement_csv(path)

    def test_empty_statement_writes_header_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            statement_dir = root / "statements"
            transaction_dir = root / "transactions"
            write_statement(statement_dir / "empty.csv", [])

            result = process_cmb_transactions(statement_dir, transaction_dir)
            header, rows = read_output(transaction_dir / "empty.csv")

        self.assertEqual(result, CmbCleaningBatchResult(1, 1, 1, 1, 0, 0))
        self.assertEqual(tuple(header), OUTPUT_FIELDS)
        self.assertEqual(rows, [])


class CmbTransactionIdTests(unittest.TestCase):
    def test_transaction_id_is_stable_and_has_fixed_format(self) -> None:
        transaction_id = build_transaction_id("sample.eml", 1)
        payload = "cmb\0sample.eml\0".encode("utf-8") + b"1"
        expected = hashlib.sha256(payload).hexdigest()[:24]

        self.assertEqual(transaction_id, build_transaction_id("sample.eml", 1))
        self.assertEqual(transaction_id, f"cmb_{expected}")
        self.assertRegex(transaction_id, r"^cmb_[0-9a-f]{24}$")

    def test_different_source_identity_generates_different_id(self) -> None:
        original = build_transaction_id("sample.eml", 1)

        self.assertNotEqual(original, build_transaction_id("sample.eml", 2))
        self.assertNotEqual(original, build_transaction_id("other.eml", 1))

    def test_description_and_amount_changes_do_not_change_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row()])
            original = read_cmb_statement_csv(path)[0].transaction_id
            write_statement(
                path,
                [statement_row(description="Changed-Description", raw_amount="999.99")],
            )
            changed = read_cmb_statement_csv(path)[0].transaction_id

        self.assertEqual(original, changed)

    def test_batch_detects_duplicate_transaction_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            statement_dir = root / "statements"
            transaction_dir = root / "transactions"
            write_statement(statement_dir / "a.csv", [statement_row(source_email="a.eml")])
            write_statement(statement_dir / "b.csv", [statement_row(source_email="b.eml")])

            with patch.object(
                cmb_transactions,
                "build_transaction_id",
                return_value="cmb_" + "0" * 24,
            ):
                result = process_cmb_transactions(statement_dir, transaction_dir)

        self.assertEqual(result.statements, 2)
        self.assertEqual(result.written, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.transactions, 1)


class CmbTransactionDateTests(unittest.TestCase):
    def test_same_year_date_completion(self) -> None:
        self.assertEqual(complete_mmdd("0701", date(2026, 7, 10)), date(2026, 7, 1))

    def test_january_statement_completes_december_to_previous_year(self) -> None:
        self.assertEqual(complete_mmdd("1231", date(2026, 1, 10)), date(2025, 12, 31))

    def test_transaction_and_post_dates_complete_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(
                path,
                [
                    statement_row(
                        email_date="2026-01-10",
                        transaction_mmdd="1231",
                        post_mmdd="0102",
                    )
                ],
            )

            transaction = read_cmb_statement_csv(path)[0]

        self.assertEqual(transaction.transaction_date, date(2025, 12, 31))
        self.assertEqual(transaction.post_date, date(2026, 1, 2))

    def test_invalid_mmdd_fails_file(self) -> None:
        for field, value in (
            ("transaction_mmdd", "701"),
            ("post_mmdd", "07A2"),
            ("transaction_mmdd", "0230"),
        ):
            with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "sample.csv"
                write_statement(path, [statement_row(**{field: value})])
                with self.assertRaises(CmbStatementContractError):
                    read_cmb_statement_csv(path)

    def test_completed_date_cannot_be_later_than_email_date(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(transaction_mmdd="0711")])

            with self.assertRaisesRegex(CmbStatementContractError, "later"):
                read_cmb_statement_csv(path)

    def test_post_date_may_precede_transaction_date_and_output_is_iso(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "sample.csv"
            output_path = root / "output.csv"
            write_statement(input_path, [statement_row(transaction_mmdd="0705", post_mmdd="0704")])
            write_cleaned_transactions_csv(read_cmb_statement_csv(input_path), output_path)
            _, rows = read_output(output_path)

        self.assertEqual(rows[0]["transaction_date"], "2026-07-05")
        self.assertEqual(rows[0]["post_date"], "2026-07-04")


class CmbDescriptionTests(unittest.TestCase):
    def test_description_prefix_is_not_parsed(self) -> None:
        description = "消费金抵扣-招财红包-扫码-Example Merchant"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(description=description)])

            transaction = read_cmb_statement_csv(path)[0]

        self.assertEqual(transaction.bank_description, description)
        self.assertEqual(transaction.payment_channel, "")
        self.assertEqual(transaction.merchant_raw, description)

    def test_description_only_receives_edge_whitespace_cleanup(self) -> None:
        description = "eXaMpLe-Merchant  Branch"
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(description=f"  {description}  ")])

            transaction = read_cmb_statement_csv(path)[0]

        self.assertEqual(transaction.bank_description, description)
        self.assertEqual(transaction.merchant_raw, description)


class CmbTransactionAmountTests(unittest.TestCase):
    def test_amounts_use_decimal_and_preserve_numeric_sign(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement(path, [statement_row(raw_amount="-12.30", cny_amount="12.30")])

            transaction = read_cmb_statement_csv(path)[0]

        self.assertIsInstance(transaction.raw_amount, Decimal)
        self.assertIsInstance(transaction.cny_amount, Decimal)
        self.assertEqual(transaction.raw_amount, Decimal("-12.30"))
        self.assertEqual(transaction.cny_amount, Decimal("12.30"))

    def test_either_amount_may_be_empty(self) -> None:
        cases = (
            (statement_row(raw_amount=""), None, Decimal("88.88")),
            (statement_row(cny_amount=""), Decimal("12.30"), None),
        )
        for row, expected_raw, expected_cny in cases:
            with self.subTest(row=row), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "sample.csv"
                write_statement(path, [row])
                transaction = read_cmb_statement_csv(path)[0]
                self.assertEqual(transaction.raw_amount, expected_raw)
                self.assertEqual(transaction.cny_amount, expected_cny)

    def test_output_uses_plain_decimal_and_preserves_raw_amount_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "sample.csv"
            output_path = root / "output.csv"
            write_statement(
                input_path,
                [
                    statement_row(
                        raw_amount_text=" RAW 1E+3 ",
                        raw_amount="1E+3",
                        cny_amount_text=" CNY 1E-7 ",
                        cny_amount="1E-7",
                    )
                ],
            )
            write_cleaned_transactions_csv(read_cmb_statement_csv(input_path), output_path)
            _, rows = read_output(output_path)

        self.assertEqual(rows[0]["raw_amount"], "1000")
        self.assertEqual(rows[0]["cny_amount"], "0.0000001")
        self.assertNotIn("E", rows[0]["raw_amount"])
        self.assertNotIn("E", rows[0]["cny_amount"])
        self.assertEqual(rows[0]["raw_amount_text"], " RAW 1E+3 ")
        self.assertEqual(rows[0]["cny_amount_text"], " CNY 1E-7 ")


class CmbTransactionOutputTests(unittest.TestCase):
    def test_output_has_fixed_fields_bom_same_stem_and_input_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            statement_dir = root / "statements"
            transaction_dir = root / "transactions"
            input_path = statement_dir / "sample.csv"
            write_statement(
                input_path,
                [
                    statement_row(description="First"),
                    statement_row(transaction_index="2", description="Second"),
                ],
            )

            result = process_cmb_transactions(statement_dir, transaction_dir)
            output_path = transaction_dir / "sample.csv"
            header, rows = read_output(output_path)
            output_bytes = output_path.read_bytes()

        self.assertEqual(result, CmbCleaningBatchResult(1, 1, 1, 0, 0, 2))
        self.assertEqual(output_path.name, input_path.name)
        self.assertTrue(output_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(tuple(header), OUTPUT_FIELDS)
        self.assertEqual([row["bank_description"] for row in rows], ["First", "Second"])

    def test_repeated_run_is_byte_identical_and_input_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            statement_dir = root / "statements"
            transaction_dir = root / "transactions"
            input_path = statement_dir / "sample.csv"
            write_statement(input_path, [statement_row()])
            original_input = input_path.read_bytes()

            process_cmb_transactions(statement_dir, transaction_dir)
            output_path = transaction_dir / "sample.csv"
            first_output = output_path.read_bytes()
            process_cmb_transactions(statement_dir, transaction_dir)
            second_output = output_path.read_bytes()
            final_input = input_path.read_bytes()

        self.assertEqual(first_output, second_output)
        self.assertEqual(final_input, original_input)

    def test_atomic_replace_overwrites_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "sample.csv"
            output_path = root / "output.csv"
            output_path.write_bytes(b"old")
            write_statement(input_path, [statement_row()])

            write_cleaned_transactions_csv(read_cmb_statement_csv(input_path), output_path)
            output_bytes = output_path.read_bytes()

        self.assertNotEqual(output_bytes, b"old")

    def test_write_failure_preserves_existing_output_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "sample.csv"
            output_path = root / "output.csv"
            output_path.write_bytes(b"old")
            write_statement(input_path, [statement_row()])
            transactions = read_cmb_statement_csv(input_path)

            with patch.object(cmb_transactions.os, "replace", side_effect=OSError("failed")):
                with self.assertRaises(OSError):
                    write_cleaned_transactions_csv(transactions, output_path)

            temp_files = list(root.glob(".output.csv.*.tmp"))
            output_bytes = output_path.read_bytes()

        self.assertEqual(output_bytes, b"old")
        self.assertEqual(temp_files, [])

    def test_bad_file_does_not_block_other_files_or_replace_old_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            statement_dir = root / "statements"
            transaction_dir = root / "transactions"
            write_statement(
                statement_dir / "a-bad.csv",
                [statement_row(source_email="a-bad.eml", description="")],
            )
            write_statement(
                statement_dir / "b-good.csv",
                [statement_row(source_email="b-good.eml")],
            )
            bad_output = transaction_dir / "a-bad.csv"
            bad_output.parent.mkdir(parents=True, exist_ok=True)
            bad_output.write_bytes(b"existing")

            result = process_cmb_transactions(statement_dir, transaction_dir)
            bad_output_bytes = bad_output.read_bytes()
            good_output_exists = (transaction_dir / "b-good.csv").exists()

        self.assertEqual(result, CmbCleaningBatchResult(2, 1, 1, 0, 1, 1))
        self.assertEqual(bad_output_bytes, b"existing")
        self.assertTrue(good_output_exists)

    def test_statement_files_are_processed_in_filename_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            statement_dir = root / "statements"
            transaction_dir = root / "transactions"
            write_statement(statement_dir / "b.csv", [])
            write_statement(statement_dir / "a.csv", [])
            observed: list[str] = []
            original_reader = cmb_transactions.read_cmb_statement_csv

            def recording_reader(path: Path):
                observed.append(path.name)
                return original_reader(path)

            with patch.object(cmb_transactions, "read_cmb_statement_csv", side_effect=recording_reader):
                process_cmb_transactions(statement_dir, transaction_dir)

        self.assertEqual(observed, ["a.csv", "b.csv"])

    def test_import_has_no_configuration_or_filesystem_side_effects(self) -> None:
        source_root = Path(cmb_transactions.__file__).parents[2]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "should-not-exist"
            (root / ".env").write_text(
                f"CMB_TRANSACTION_DIR={target}\nIMPORT_SENTINEL=loaded\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("IMPORT_SENTINEL", None)
            env["PYTHONPATH"] = str(source_root)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os; "
                        "import family_spending.cleaning.cmb_transactions; "
                        "print(os.environ.get('IMPORT_SENTINEL', ''))"
                    ),
                ],
                cwd=root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.stdout.strip(), "")
        self.assertFalse(target.exists())

    def test_load_config_uses_defaults_and_explicit_environment(self) -> None:
        defaults = load_config({})
        explicit = load_config(
            {
                "CMB_STATEMENT_DIR": "input",
                "CMB_TRANSACTION_DIR": "output",
            }
        )

        self.assertEqual(defaults.statement_dir, Path("data/statements/cmb"))
        self.assertEqual(defaults.transaction_dir, Path("data/transactions/cmb"))
        self.assertEqual(explicit.statement_dir, Path("input"))
        self.assertEqual(explicit.transaction_dir, Path("output"))


if __name__ == "__main__":
    unittest.main()
