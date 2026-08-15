from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from email.message import EmailMessage

from family_spending.sources.cmb_email.evidence import CmbEmailEvidence
from family_spending.sources.cmb_email.parser import (
    CmbEmailParseError,
    complete_mmdd,
    parse_cmb_email,
)
from family_spending.sources.cmb_email.source import CmbEmailSource


def table(cells: list[str], *, width: str = "643", height: str = "18") -> str:
    body = "".join(f"<td>{cell}</td>" for cell in cells)
    return f'<table width="{width}" height="{height}"><tr>{body}</tr></table>'


def transaction_row(mmdd: str, post_mmdd: str, description: str, amount: str) -> str:
    return table(
        ["", mmdd, post_mmdd, description, amount, "4529", "CN", amount.replace("\u00a5 ", "")]
    )


def repayment_row(post_mmdd: str = "0827") -> str:
    return table(
        ["", "", post_mmdd, "\u8de8\u884c\u8f6c\u8d26\u8fd8\u6b3e", "\u00a5 -100.00", "4529", "", "-100.00"]
    )


def make_email(
    html_parts: list[str],
    email_date: str = "Wed, 10 Sep 2025 08:00:00 +0800",
) -> bytes:
    message = EmailMessage()
    message["Date"] = email_date
    message["Subject"] = "\u62db\u5546\u94f6\u884c\u4fe1\u7528\u5361\u7535\u5b50\u8d26\u5355"
    message.set_content("fallback")
    for html in html_parts:
        message.add_alternative(html, subtype="html", charset="utf-8")
    return message.as_bytes()


class _Reader:
    def __init__(self, evidence: tuple[CmbEmailEvidence, ...]) -> None:
        self._evidence = evidence

    def load_all(self) -> tuple[CmbEmailEvidence, ...]:
        return self._evidence


class CmbEmailSourceTests(unittest.TestCase):
    def test_evidence_identity_is_raw_bytes_content_hash(self) -> None:
        raw = make_email([transaction_row("0811", "0812", "merchant", "\u00a5 -7.81")])
        first = CmbEmailEvidence(raw)
        second = CmbEmailEvidence(raw)
        self.assertEqual(first.identity, second.identity)
        self.assertTrue(first.identity.startswith("sha256:"))
        self.assertEqual(first.filename, f"{first.digest}.eml")

    def test_parse_transaction_and_skip_repayment(self) -> None:
        raw = make_email(
            [
                repayment_row()
                + transaction_row("0811", "0812", "\u652f\u4ed8\u5b9d-\u6d4b\u8bd5\u5546\u6237", "\u00a5 -7.81")
            ]
        )
        parsed = parse_cmb_email(CmbEmailEvidence(raw))

        self.assertEqual(parsed.skipped_repayments, 1)
        self.assertEqual(len(parsed.records), 1)
        record = parsed.records[0]
        self.assertEqual(record.transaction_date, date(2025, 8, 11))
        self.assertEqual(record.amount, Decimal("-7.81"))
        self.assertEqual(record.description, "\u652f\u4ed8\u5b9d-\u6d4b\u8bd5\u5546\u6237")
        self.assertEqual(record.source_type, "cmb_email")
        self.assertTrue(record.identity.record_locator.endswith("/table:2"))

    def test_repeated_parse_of_same_eml_keeps_source_ids(self) -> None:
        raw = make_email(
            [
                transaction_row("0811", "0812", "first", "\u00a5 -7.81")
                + transaction_row("0812", "0813", "second", "\u00a5 -8.00")
            ]
        )
        evidence = CmbEmailEvidence(raw)
        first = parse_cmb_email(evidence)
        second = parse_cmb_email(evidence)
        self.assertEqual(
            tuple(record.id for record in first.records),
            tuple(record.id for record in second.records),
        )

    def test_duplicate_looking_rows_have_distinct_dom_locators(self) -> None:
        row = transaction_row("0811", "0812", "same", "\u00a5 2.00")
        parsed = parse_cmb_email(CmbEmailEvidence(make_email([row + row])))
        self.assertEqual(len(parsed.records), 2)
        self.assertNotEqual(parsed.records[0].id, parsed.records[1].id)
        self.assertTrue(parsed.records[0].identity.record_locator.endswith("/table:1"))
        self.assertTrue(parsed.records[1].identity.record_locator.endswith("/table:2"))

    def test_locator_counts_ignored_tables_not_emitted_records(self) -> None:
        ignored = table(["not", "a", "transaction"])
        transaction = transaction_row("0811", "0812", "merchant", "\u00a5 1.00")
        parsed = parse_cmb_email(CmbEmailEvidence(make_email([ignored + transaction])))
        self.assertEqual(len(parsed.records), 1)
        self.assertTrue(parsed.records[0].identity.record_locator.endswith("/table:2"))

    def test_previous_year_is_completed_from_email_date(self) -> None:
        self.assertEqual(complete_mmdd("1210", date(2026, 1, 10)), date(2025, 12, 10))

    def test_unexpected_date_less_row_fails(self) -> None:
        unexpected = table(["", "", "0827", "adjustment", "\u00a5 -100.00", "4529", "", "-100.00"])
        with self.assertRaisesRegex(CmbEmailParseError, "Unexpected date-less row"):
            parse_cmb_email(CmbEmailEvidence(make_email([unexpected])))

    def test_invalid_amount_fails(self) -> None:
        bad = transaction_row("0811", "0812", "merchant", "not-an-amount")
        with self.assertRaisesRegex(CmbEmailParseError, "Invalid CMB amount"):
            parse_cmb_email(CmbEmailEvidence(make_email([bad])))

    def test_multiple_transaction_html_parts_fail(self) -> None:
        first = transaction_row("0811", "0812", "first", "\u00a5 1.00")
        second = transaction_row("0812", "0813", "second", "\u00a5 2.00")
        with self.assertRaisesRegex(CmbEmailParseError, "Multiple CMB transaction HTML parts"):
            parse_cmb_email(CmbEmailEvidence(make_email([first, second])))

    def test_source_reads_evidence_without_transactions_csv(self) -> None:
        raw = make_email([transaction_row("0811", "0812", "merchant", "\u00a5 1.00")])
        source = CmbEmailSource(_Reader((CmbEmailEvidence(raw),)))
        records = source.load_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_type, "cmb_email")


if __name__ == "__main__":
    unittest.main()
