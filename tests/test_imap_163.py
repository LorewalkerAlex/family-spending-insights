from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

from loguru import logger

from family_spending.ingestion.imap_163 import (
    HEADER_QUERY,
    RAW_QUERY,
    Imap163Config,
    build_email_filename,
    encode_mailbox_name,
    fetch_raw_emails,
    load_config,
    match_keywords,
    parse_since_date,
    save_raw_email,
)

logger.remove()


class FakeImap:
    """Record protocol calls so tests verify behavior without network access."""

    def __init__(
        self,
        headers: dict[bytes, bytes],
        raw_messages: dict[bytes, bytes],
        search_ids: bytes,
        list_data: list[bytes] | None = None,
    ) -> None:
        self.headers = headers
        self.raw_messages = raw_messages
        self.search_ids = search_ids
        self.list_data = list_data or []
        self.calls: list[tuple[object, ...]] = []
        self.fetch_calls: list[tuple[bytes, str]] = []

    def login(self, email_addr: str, auth_code: str):
        self.calls.append(("login", email_addr, auth_code))
        return "OK", [b"logged in"]

    def _simple_command(self, command: str, payload: str):
        self.calls.append(("id", command, payload))
        return "OK", [b"id accepted"]

    def list(self):
        self.calls.append(("list",))
        return "OK", self.list_data

    def select(self, mailbox: str, readonly: bool = False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def search(self, charset, criterion: str, since: str):
        self.calls.append(("search", charset, criterion, since))
        return "OK", [self.search_ids]

    def fetch(self, mail_id: bytes, query: str):
        self.fetch_calls.append((mail_id, query))

        payload = (
            self.headers.get(mail_id)
            if query == HEADER_QUERY
            else self.raw_messages.get(mail_id)
        )

        if payload is None:
            return "NO", [b"missing"]

        return "OK", [(b"metadata", payload), b")"]

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b"logged out"]


class Imap163Tests(unittest.TestCase):
    """Protect the raw-email ingestion contract with deterministic fake data."""

    def make_config(
        self,
        out_dir: Path,
        mailboxes: tuple[str, ...] | None = ("INBOX",),
    ) -> Imap163Config:
        return Imap163Config(
            host="imap.example.test",
            port=993,
            email_addr="user@example.test",
            auth_code="fake-auth-code",
            since="01-Jan-2026",
            since_date=date(2026, 1, 1),
            mailboxes=mailboxes,
            keywords=("cmb", "招商银行"),
            out_dir=out_dir,
        )

    def test_parse_since_date(self) -> None:
        self.assertEqual(parse_since_date("01-Jan-2023"), date(2023, 1, 1))

    def test_keyword_matching_is_case_insensitive(self) -> None:
        self.assertTrue(
            match_keywords(
                "CMB STATEMENT",
                "sender@example.test",
                ("cmb",),
            )
        )

    def test_keyword_matching_uses_or_semantics(self) -> None:
        self.assertTrue(
            match_keywords(
                "Monthly statement",
                "notice@cmb.example",
                ("missing", "cmb"),
            )
        )
        self.assertFalse(
            match_keywords(
                "Monthly statement",
                "notice@example.test",
                ("missing", "also-missing"),
            )
        )

    def test_ascii_mailbox_is_unchanged(self) -> None:
        self.assertEqual(
            encode_mailbox_name("INBOX/Archive"),
            "INBOX/Archive",
        )

    def test_chinese_mailbox_uses_modified_utf7(self) -> None:
        self.assertEqual(
            encode_mailbox_name("招商银行"),
            "&YttVRpT2iEw-",
        )

    def test_message_id_generates_stable_filename(self) -> None:
        first = build_email_filename(
            date(2026, 7, 10),
            " <same@example.test> ",
            b"first",
        )
        second = build_email_filename(
            date(2026, 7, 10),
            "same@example.test",
            b"second",
        )

        self.assertEqual(first, second)

    def test_same_message_id_is_independent_of_mailbox(self) -> None:
        inbox = build_email_filename(
            date(2026, 7, 10),
            "<same@example.test>",
        )
        archive = build_email_filename(
            date(2026, 7, 10),
            "<same@example.test>",
        )

        self.assertEqual(inbox, archive)

    def test_missing_message_id_uses_raw_bytes_hash(self) -> None:
        raw_message = b"Subject: fake\r\n\r\nbody\r\n"
        filename = build_email_filename(
            date(2026, 7, 10),
            None,
            raw_message,
        )

        self.assertIn(
            hashlib.sha256(raw_message).hexdigest()[:24],
            filename,
        )

    def test_same_day_different_messages_have_different_names(self) -> None:
        first = build_email_filename(
            date(2026, 7, 10),
            None,
            b"first",
        )
        second = build_email_filename(
            date(2026, 7, 10),
            None,
            b"second",
        )

        self.assertNotEqual(first, second)

    def test_saved_bytes_are_exact(self) -> None:
        raw_message = b"Subject: fake\r\nX-Test: \xff\r\n\r\nbody\x00\r\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "message.eml"

            self.assertTrue(save_raw_email(path, raw_message))
            self.assertEqual(path.read_bytes(), raw_message)

    def test_existing_email_is_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "message.eml"
            path.write_bytes(b"existing")

            self.assertFalse(save_raw_email(path, b"replacement"))
            self.assertEqual(path.read_bytes(), b"existing")

    def test_unmatched_keyword_does_not_fetch_raw_message(self) -> None:
        header = (
            b"Subject: unrelated\r\n"
            b"From: sender@example.test\r\n"
            b"Date: Fri, 10 Jul 2026 08:00:00 +0800\r\n"
            b"Message-ID: <unmatched@example.test>\r\n"
            b"\r\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            fake = FakeImap(
                {b"1": header},
                {b"1": b"raw"},
                b"1",
            )
            summary = fetch_raw_emails(
                self.make_config(Path(temp_dir)),
                imap_factory=lambda host, port: fake,
            )

        self.assertEqual(summary.keyword_skipped, 1)
        self.assertNotIn((b"1", RAW_QUERY), fake.fetch_calls)

    def test_old_email_does_not_fetch_raw_message(self) -> None:
        header = (
            b"Subject: CMB statement\r\n"
            b"From: sender@example.test\r\n"
            b"Date: Wed, 10 Dec 2025 08:00:00 +0800\r\n"
            b"Message-ID: <old@example.test>\r\n"
            b"\r\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            fake = FakeImap(
                {b"1": header},
                {b"1": b"raw"},
                b"1",
            )
            summary = fetch_raw_emails(
                self.make_config(Path(temp_dir)),
                imap_factory=lambda host, port: fake,
            )

        self.assertEqual(summary.date_skipped, 1)
        self.assertNotIn((b"1", RAW_QUERY), fake.fetch_calls)

    def test_complete_flow_summary_and_duplicate_skip(self) -> None:
        matched = (
            b"Subject: CMB statement\r\n"
            b"From: sender@example.test\r\n"
            b"Date: Fri, 10 Jul 2026 08:00:00 +0800\r\n"
            b"Message-ID: <same@example.test>\r\n"
            b"\r\n"
        )
        unmatched = (
            b"Subject: unrelated\r\n"
            b"From: sender@example.test\r\n"
            b"Date: Fri, 10 Jul 2026 08:00:00 +0800\r\n"
            b"Message-ID: <unmatched@example.test>\r\n"
            b"\r\n"
        )
        old = (
            b"Subject: CMB old statement\r\n"
            b"From: sender@example.test\r\n"
            b"Date: Wed, 10 Dec 2025 08:00:00 +0800\r\n"
            b"Message-ID: <old@example.test>\r\n"
            b"\r\n"
        )
        raw_message = b"Subject: CMB statement\r\n\r\nfake body\r\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = Path(temp_dir)
            fake = FakeImap(
                {
                    b"1": matched,
                    b"2": unmatched,
                    b"3": old,
                    b"4": matched,
                },
                {b"1": raw_message},
                b"1 2 3 4",
            )
            summary = fetch_raw_emails(
                self.make_config(out_dir),
                imap_factory=lambda host, port: fake,
            )
            saved_files = list(out_dir.glob("*.eml"))

        self.assertEqual(summary.mailboxes, 1)
        self.assertEqual(summary.candidates, 4)
        self.assertEqual(summary.headers, 4)
        self.assertEqual(summary.date_skipped, 1)
        self.assertEqual(summary.keyword_skipped, 1)
        self.assertEqual(summary.matched, 2)
        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.existing, 1)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(len(saved_files), 1)
        self.assertEqual(
            fake.fetch_calls.count((b"1", RAW_QUERY)),
            1,
        )
        self.assertNotIn((b"4", RAW_QUERY), fake.fetch_calls)

        select_call = next(
            call for call in fake.calls if call[0] == "select"
        )
        self.assertTrue(select_call[2])

        id_index = next(
            index
            for index, call in enumerate(fake.calls)
            if call[0] == "id"
        )
        select_index = next(
            index
            for index, call in enumerate(fake.calls)
            if call[0] == "select"
        )
        self.assertLess(id_index, select_index)

    def test_all_mailboxes_are_listed_and_scanned(self) -> None:
        fake = FakeImap(
            {},
            {},
            b"",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "&YttVRpT2iEw-"',
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            summary = fetch_raw_emails(
                self.make_config(
                    Path(temp_dir),
                    mailboxes=None,
                ),
                imap_factory=lambda host, port: fake,
            )

        selected = [
            call[1]
            for call in fake.calls
            if call[0] == "select"
        ]

        self.assertEqual(summary.mailboxes, 2)
        self.assertEqual(
            selected,
            ["INBOX", "&YttVRpT2iEw-"],
        )

    def test_missing_required_config_is_clear(self) -> None:
        base = {
            "EMAIL_ADDR": "user@example.test",
            "EMAIL_AUTH_CODE": "fake-auth-code",
            "KEYWORDS": "cmb",
        }

        for key in (
            "EMAIL_ADDR",
            "EMAIL_AUTH_CODE",
            "KEYWORDS",
        ):
            with self.subTest(key=key):
                environ = dict(base)
                environ[key] = ""

                with self.assertRaisesRegex(ValueError, key):
                    load_config(environ)

    def test_config_defaults_and_comma_separated_values(self) -> None:
        config = load_config(
            {
                "EMAIL_ADDR": "user@example.test",
                "EMAIL_AUTH_CODE": "fake-auth-code",
                "KEYWORDS": "cmb, 招商银行",
                "MAILBOXES": "INBOX, 已发送",
            }
        )

        self.assertEqual(config.host, "imap.163.com")
        self.assertEqual(config.port, 993)
        self.assertEqual(config.since, "01-Jan-2023")
        self.assertEqual(config.mailboxes, ("INBOX", "已发送"))
        self.assertEqual(config.keywords, ("cmb", "招商银行"))
        self.assertEqual(
            config.out_dir,
            Path("data/raw/emails/163/cmb"),
        )


if __name__ == "__main__":
    unittest.main()