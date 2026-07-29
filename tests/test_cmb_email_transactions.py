from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path

from loguru import logger

from family_spending.ingestion.cmb_email_transactions import (
    CSV_FIELDS,
    CmbEmailTransactionError,
    build_transaction_id,
    complete_mmdd,
    parse_cmb_email,
    rebuild_transactions,
)


def table(cells: list[str], *, width: str = "643", height: str = "18") -> str:
    body = "".join(f"<td>{cell}</td>" for cell in cells)
    return f'<table width="{width}" height="{height}"><tr>{body}</tr></table>'


def transaction_row(mmdd: str, post_mmdd: str, description: str, amount: str) -> str:
    return table(["", mmdd, post_mmdd, description, amount, "4529", "CN", amount.replace("¥ ", "")])


def repayment_row(post_mmdd: str = "0827") -> str:
    return table(["", "", post_mmdd, "跨行转账还款", "¥ -100.00", "4529", "", "-100.00"])


def make_email(
    html_parts: list[str],
    email_date: str = "Wed, 10 Sep 2025 08:00:00 +0800",
) -> bytes:
    message = EmailMessage()
    message["Date"] = email_date
    message["Subject"] = "招商银行信用卡电子账单"
    message.set_content("fallback")

    for html in html_parts:
        message.add_alternative(html, subtype="html", charset="utf-8")

    return message.as_bytes()


class CmbEmailTransactionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        logger.disable("family_spending.ingestion.cmb_email_transactions")

    @classmethod
    def tearDownClass(cls) -> None:
        logger.enable("family_spending.ingestion.cmb_email_transactions")

    def test_parse_transaction_and_skip_repayment(self) -> None:
        html = repayment_row() + transaction_row("0811", "0812", "支付宝-测试商户", "¥ -7.81")
        parsed = parse_cmb_email(make_email([html]), "statement.eml")

        self.assertEqual(parsed.skipped_repayments, 1)
        self.assertEqual(len(parsed.transactions), 1)

        transaction = parsed.transactions[0]
        self.assertEqual(transaction.transaction_date, date(2025, 8, 11))
        self.assertEqual(transaction.amount, Decimal("-7.81"))
        self.assertEqual(transaction.description, "支付宝-测试商户")
        self.assertEqual(transaction.source_email, "statement.eml")
        self.assertEqual(transaction.source_index, 1)

    def test_previous_year_is_completed_from_email_date(self) -> None:
        self.assertEqual(complete_mmdd("1210", date(2026, 1, 10)), date(2025, 12, 10))

    def test_duplicate_looking_rows_keep_distinct_ids(self) -> None:
        row = transaction_row("0811", "0812", "支付宝-相同商户", "¥ 2.00")
        parsed = parse_cmb_email(make_email([row + row]), "statement.eml")

        self.assertEqual(len(parsed.transactions), 2)
        self.assertNotEqual(parsed.transactions[0].transaction_id, parsed.transactions[1].transaction_id)
        self.assertEqual(parsed.transactions[0].source_index, 1)
        self.assertEqual(parsed.transactions[1].source_index, 2)

    def test_transaction_id_is_stable(self) -> None:
        self.assertEqual(build_transaction_id("statement.eml", 3), build_transaction_id("statement.eml", 3))
        self.assertNotEqual(build_transaction_id("statement.eml", 3), build_transaction_id("statement.eml", 4))

    def test_unexpected_date_less_row_fails(self) -> None:
        unexpected = table(["", "", "0827", "未知调整", "¥ -100.00", "4529", "", "-100.00"])

        with self.assertRaisesRegex(CmbEmailTransactionError, "Unexpected date-less row"):
            parse_cmb_email(make_email([unexpected]), "statement.eml")

    def test_invalid_transaction_amount_fails(self) -> None:
        bad = transaction_row("0811", "0812", "支付宝-测试商户", "not-an-amount")

        with self.assertRaisesRegex(CmbEmailTransactionError, "Invalid CMB amount"):
            parse_cmb_email(make_email([bad]), "statement.eml")

    def test_missing_transaction_html_fails(self) -> None:
        unrelated = table(["not", "a", "transaction"], width="100", height="20")

        with self.assertRaisesRegex(CmbEmailTransactionError, "No CMB transaction table"):
            parse_cmb_email(make_email([unrelated]), "statement.eml")

    def test_multiple_transaction_html_parts_fail(self) -> None:
        first = transaction_row("0811", "0812", "支付宝-测试商户", "¥ 1.00")
        second = transaction_row("0812", "0813", "支付宝-另一商户", "¥ 2.00")

        with self.assertRaisesRegex(CmbEmailTransactionError, "Multiple CMB transaction HTML parts"):
            parse_cmb_email(make_email([first, second]), "statement.eml")

    def test_rebuild_writes_six_fields_in_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            email_dir = root / "emails"
            output_path = root / "transactions.csv"
            email_dir.mkdir()

            later = transaction_row("0908", "0909", "支付宝-后发生", "¥ 20.00")
            earlier = repayment_row() + transaction_row("0811", "0812", "支付宝-先发生", "¥ -7.81")
            (email_dir / "b.eml").write_bytes(make_email([later]))
            (email_dir / "a.eml").write_bytes(make_email([earlier]))

            summary = rebuild_transactions(email_dir, output_path)

            with output_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(summary.emails, 2)
        self.assertEqual(summary.transactions, 2)
        self.assertEqual(summary.skipped_repayments, 1)
        self.assertEqual(tuple(rows[0].keys()), CSV_FIELDS)
        self.assertEqual(rows[0]["description"], "支付宝-先发生")
        self.assertEqual(rows[0]["amount"], "-7.81")
        self.assertEqual(rows[0]["source_email"], "a.eml")
        self.assertEqual(rows[0]["source_index"], "1")
        self.assertEqual(rows[1]["description"], "支付宝-后发生")

    def test_failed_rebuild_preserves_existing_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            email_dir = root / "emails"
            output_path = root / "transactions.csv"
            email_dir.mkdir()
            output_path.write_text("existing\n", encoding="utf-8")

            good = transaction_row("0811", "0812", "支付宝-测试商户", "¥ 1.00")
            bad = transaction_row("0811", "0812", "支付宝-错误金额", "bad")
            (email_dir / "a.eml").write_bytes(make_email([good]))
            (email_dir / "b.eml").write_bytes(make_email([bad]))

            with self.assertRaises(CmbEmailTransactionError):
                rebuild_transactions(email_dir, output_path)

            self.assertEqual(output_path.read_text(encoding="utf-8"), "existing\n")


if __name__ == "__main__":
    unittest.main()