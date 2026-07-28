from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from email import policy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from loguru import logger

from family_spending.ingestion import cmb_statement
from family_spending.ingestion.cmb_statement import (
    CSV_FIELDS,
    CmbStatement,
    CmbStatementAmbiguityError,
    CmbStatementError,
    CmbRawTransaction,
    extract_cmb_transactions_from_html,
    load_config,
    normalize_text,
    parse_amount,
    parse_cmb_statement,
    parse_cmb_statement_file,
    process_cmb_statements,
    write_statement_csv,
)

logger.remove()

VALID_DATE = "Fri, 10 Jul 2026 08:00:00 +0800"


def transaction_table(
    transaction_mmdd: str = "0701",
    post_mmdd: str = "0702",
    description: str = "Sample Merchant",
    raw_amount_text: str = "¥12.30",
    card_last4: str = "1234",
    country_or_region: str = "CN",
    cny_amount_text: str = "￥12.30",
    width: str = "643",
    height: str = "18",
) -> str:
    cells = (
        "",
        transaction_mmdd,
        post_mmdd,
        description,
        raw_amount_text,
        card_last4,
        country_or_region,
        cny_amount_text,
    )
    return (
        f'<table width="{width}" height="{height}"><tr>'
        + "".join(f"<td>{value}</td>" for value in cells)
        + "</tr></table>"
    )


def html_message(
    html: str,
    *,
    cte: str = "quoted-printable",
    charset: str = "utf-8",
    date_header: str = VALID_DATE,
    message_id: str = "<statement@example.test>",
) -> bytes:
    message = EmailMessage()
    message["Date"] = date_header
    message["Message-ID"] = message_id
    message.set_content(html, subtype="html", charset=charset, cte=cte)
    return message.as_bytes(policy=policy.SMTP)


def text_message(text: str = "No HTML") -> bytes:
    message = EmailMessage()
    message["Date"] = VALID_DATE
    message["Message-ID"] = "<plain@example.test>"
    message.set_content(text)
    return message.as_bytes(policy=policy.SMTP)


def multipart_message(
    html_parts: list[str],
    *,
    attachment_parts: list[str] | None = None,
) -> bytes:
    message = EmailMessage()
    message["Date"] = VALID_DATE
    message["Message-ID"] = "<multipart@example.test>"
    message.make_mixed()

    plain = EmailMessage()
    plain.set_content("Plain text is not a CMB HTML statement.")
    message.attach(plain)

    for index, html in enumerate(html_parts, start=1):
        part = EmailMessage()
        part.set_content(html, subtype="html", charset="utf-8", cte="base64")
        part["Content-ID"] = f"<html-{index}@example.test>"
        message.attach(part)

    for index, html in enumerate(attachment_parts or [], start=1):
        part = EmailMessage()
        part.set_content(html, subtype="html", charset="utf-8", cte="base64")
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=f"statement-{index}.html",
        )
        message.attach(part)

    return message.as_bytes(policy=policy.SMTP)


def sample_statement(source_email: str = "sample.eml") -> CmbStatement:
    transaction = CmbRawTransaction(
        transaction_mmdd="0701",
        post_mmdd="0702",
        description="Sample Merchant",
        raw_amount_text="¥1,234.50",
        raw_amount=Decimal("1234.50"),
        card_last4="1234",
        country_or_region="CN",
        cny_amount_text="￥1,234.50",
        cny_amount=Decimal("1234.50"),
    )
    return CmbStatement(
        source_email=source_email,
        email_date=date(2026, 7, 10),
        message_id="<sample@example.test>",
        transactions=(transaction,),
    )


class CmbStatementTests(unittest.TestCase):
    def test_quoted_printable_utf8_html_is_decoded(self) -> None:
        raw = html_message(
            transaction_table(description="虚假商户"),
            cte="quoted-printable",
        )

        statement = parse_cmb_statement(raw, "quoted-printable.eml")

        self.assertEqual(statement.transactions[0].description, "虚假商户")

    def test_base64_html_is_decoded(self) -> None:
        raw = html_message(transaction_table(), cte="base64")

        statement = parse_cmb_statement(raw, "base64.eml")

        self.assertEqual(len(statement.transactions), 1)

    def test_declared_non_utf8_charset_is_decoded(self) -> None:
        raw = html_message(
            transaction_table(description="测试商户"),
            cte="base64",
            charset="gb18030",
        )

        statement = parse_cmb_statement(raw, "gb18030.eml")

        self.assertEqual(statement.transactions[0].description, "测试商户")

    def test_html_attachment_is_skipped(self) -> None:
        raw = multipart_message([], attachment_parts=[transaction_table()])

        statement = parse_cmb_statement(raw, "attachment.eml")

        self.assertEqual(statement.transactions, ())

    def test_no_html_part_returns_empty_statement(self) -> None:
        statement = parse_cmb_statement(
            text_message(transaction_table()),
            "plain.eml",
        )

        self.assertEqual(statement.transactions, ())

    def test_current_eight_cell_cmb_row_is_recognized(self) -> None:
        transactions = extract_cmb_transactions_from_html(transaction_table())

        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0].transaction_mmdd, "0701")
        self.assertEqual(transactions[0].post_mmdd, "0702")
        self.assertEqual(transactions[0].card_last4, "1234")
        self.assertEqual(transactions[0].country_or_region, "CN")

    def test_non_matching_table_size_is_ignored(self) -> None:
        html = transaction_table(width="318", height="47")

        self.assertEqual(extract_cmb_transactions_from_html(html), ())

    def test_invalid_mmdd_candidate_is_ignored(self) -> None:
        html = transaction_table(transaction_mmdd="07-1")

        self.assertEqual(extract_cmb_transactions_from_html(html), ())

    def test_empty_description_candidate_is_ignored(self) -> None:
        html = transaction_table(description=" \xa0 ")

        self.assertEqual(extract_cmb_transactions_from_html(html), ())

    def test_candidate_with_two_invalid_amounts_is_ignored(self) -> None:
        html = transaction_table(
            raw_amount_text="not-an-amount",
            cny_amount_text="also-invalid",
        )

        self.assertEqual(extract_cmb_transactions_from_html(html), ())

    def test_amount_symbols_commas_and_negative_values(self) -> None:
        self.assertEqual(parse_amount("¥1,234.50"), Decimal("1234.50"))
        self.assertEqual(parse_amount("￥ -12.30"), Decimal("-12.30"))
        self.assertEqual(parse_amount("&yen; 9.80"), Decimal("9.80"))
        self.assertEqual(parse_amount("+0.25"), Decimal("0.25"))

    def test_amounts_are_decimal_without_float_rounding(self) -> None:
        amount = parse_amount("¥0.10")

        self.assertIsInstance(amount, Decimal)
        assert isinstance(amount, Decimal)
        self.assertEqual(amount + Decimal("0.20"), Decimal("0.30"))

    def test_text_whitespace_is_normalized_only_structurally(self) -> None:
        self.assertEqual(
            normalize_text("  Sample\xa0  Merchant\nName  "),
            "Sample Merchant Name",
        )

    def test_transaction_order_is_preserved(self) -> None:
        html = transaction_table(description="First") + transaction_table(
            transaction_mmdd="0703",
            post_mmdd="0704",
            description="Second",
        )

        transactions = extract_cmb_transactions_from_html(html)

        self.assertEqual(
            [transaction.description for transaction in transactions],
            ["First", "Second"],
        )

    def test_identical_html_candidates_do_not_duplicate_transactions(self) -> None:
        html = transaction_table()
        raw = multipart_message([html, html])

        statement = parse_cmb_statement(raw, "duplicates.eml")

        self.assertEqual(len(statement.transactions), 1)

    def test_different_nonempty_html_candidates_raise_ambiguity(self) -> None:
        raw = multipart_message(
            [
                transaction_table(description="First"),
                transaction_table(description="Second"),
            ]
        )

        with self.assertRaises(CmbStatementAmbiguityError):
            parse_cmb_statement(raw, "ambiguous.eml")

    def test_invalid_email_date_raises_clear_error(self) -> None:
        raw = html_message(transaction_table(), date_header="not-a-date")

        with self.assertRaisesRegex(CmbStatementError, "Invalid Date header"):
            parse_cmb_statement(raw, "invalid-date.eml")

    def test_email_metadata_is_preserved_in_memory(self) -> None:
        statement = parse_cmb_statement(
            html_message(transaction_table()),
            "source.eml",
        )

        self.assertEqual(statement.source_email, "source.eml")
        self.assertEqual(statement.email_date, date(2026, 7, 10))
        self.assertEqual(statement.message_id, "<statement@example.test>")

    def test_csv_field_order_is_fixed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement_csv(sample_statement(), path)
            with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                header = next(csv.reader(csv_file))

        self.assertEqual(tuple(header), CSV_FIELDS)

    def test_csv_uses_utf8_sig_and_plain_decimal_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            write_statement_csv(sample_statement(), path)
            raw = path.read_bytes()
            with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                row = next(csv.DictReader(csv_file))

        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(row["raw_amount"], "1234.50")
        self.assertEqual(row["cny_amount"], "1234.50")
        self.assertEqual(row["transaction_index"], "1")
        self.assertNotIn("Message-ID", row)

    def test_output_uses_input_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            email_dir = root / "emails"
            statement_dir = root / "statements"
            email_dir.mkdir()
            email_path = email_dir / "2026-07-10_hash.eml"
            email_path.write_bytes(html_message(transaction_table()))

            result = process_cmb_statements(email_dir, statement_dir)

            self.assertEqual(result.written, 1)
            self.assertTrue((statement_dir / "2026-07-10_hash.csv").exists())

    def test_atomic_overwrite_replaces_file_with_complete_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            path.write_text("old partial content", encoding="utf-8")

            write_statement_csv(sample_statement(), path)

            with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_email"], "sample.eml")
            self.assertFalse(list(path.parent.glob(f".{path.name}.*.tmp")))

    def test_write_failure_preserves_existing_file_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            original = b"existing-successful-content"
            path.write_bytes(original)

            with patch.object(cmb_statement.os, "replace", side_effect=OSError):
                with self.assertRaises(OSError):
                    write_statement_csv(sample_statement(), path)

            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(path.parent.glob(f".{path.name}.*.tmp")))

    def test_batch_continues_and_summary_counts_are_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            email_dir = root / "emails"
            statement_dir = root / "statements"
            email_dir.mkdir()
            (email_dir / "a-valid.eml").write_bytes(
                html_message(transaction_table(description="First"))
            )
            (email_dir / "b-empty.eml").write_bytes(text_message())
            (email_dir / "c-invalid.eml").write_bytes(
                html_message(transaction_table(), date_header="invalid")
            )

            result = process_cmb_statements(email_dir, statement_dir)

            self.assertEqual(result.emails, 3)
            self.assertEqual(result.parsed, 2)
            self.assertEqual(result.written, 2)
            self.assertEqual(result.empty, 1)
            self.assertEqual(result.failed, 1)
            self.assertEqual(result.transactions, 1)
            self.assertTrue((statement_dir / "a-valid.csv").exists())
            self.assertTrue((statement_dir / "b-empty.csv").exists())
            self.assertFalse((statement_dir / "c-invalid.csv").exists())

    def test_batch_processes_input_filenames_in_sorted_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            email_dir = root / "emails"
            email_dir.mkdir()
            for name in ("c.eml", "a.eml", "b.eml"):
                (email_dir / name).write_bytes(text_message())

            seen: list[str] = []

            def record_write(statement: CmbStatement, output_path: Path) -> None:
                seen.append(statement.source_email)

            with patch.object(cmb_statement, "write_statement_csv", record_write):
                process_cmb_statements(email_dir, root / "statements")

        self.assertEqual(seen, ["a.eml", "b.eml", "c.eml"])

    def test_empty_statement_writes_header_only_csv(self) -> None:
        statement = CmbStatement(
            source_email="empty.eml",
            email_date=date(2026, 7, 10),
            message_id=None,
            transactions=(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "empty.csv"
            write_statement_csv(statement, path)
            with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                rows = list(csv.reader(csv_file))

        self.assertEqual(rows, [list(CSV_FIELDS)])

    def test_parsing_does_not_change_eml_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source.eml"
            original = html_message(transaction_table())
            path.write_bytes(original)

            parse_cmb_statement_file(path)

            self.assertEqual(path.read_bytes(), original)

    def test_config_defaults_and_explicit_environment(self) -> None:
        defaults = load_config({})
        explicit = load_config(
            {
                "CMB_EMAIL_DIR": "custom/emails",
                "CMB_STATEMENT_DIR": "custom/statements",
            }
        )

        self.assertEqual(defaults.email_dir, Path("data/emails/163/cmb"))
        self.assertEqual(defaults.statement_dir, Path("data/statements/cmb"))
        self.assertEqual(explicit.email_dir, Path("custom/emails"))
        self.assertEqual(explicit.statement_dir, Path("custom/statements"))

    def test_import_has_no_filesystem_or_configuration_side_effects(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        source_root = project_root / "src"
        with tempfile.TemporaryDirectory() as temp_dir:
            cwd = Path(temp_dir)
            trap_email_dir = cwd / "trap-emails"
            trap_statement_dir = cwd / "trap-statements"
            (cwd / ".env").write_text(
                "CMB_EMAIL_DIR=dotenv-emails\nCMB_STATEMENT_DIR=dotenv-statements\n",
                encoding="utf-8",
            )
            environ = os.environ.copy()
            environ["PYTHONPATH"] = str(source_root)
            environ["CMB_EMAIL_DIR"] = str(trap_email_dir)
            environ["CMB_STATEMENT_DIR"] = str(trap_statement_dir)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import family_spending.ingestion.cmb_statement",
                ],
                cwd=cwd,
                env=environ,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            self.assertFalse(trap_email_dir.exists())
            self.assertFalse(trap_statement_dir.exists())
            self.assertFalse((cwd / "dotenv-emails").exists())
            self.assertFalse((cwd / "dotenv-statements").exists())
            self.assertFalse((cwd / "data").exists())


if __name__ == "__main__":
    unittest.main()