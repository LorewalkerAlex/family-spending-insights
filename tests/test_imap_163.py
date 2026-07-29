from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date
from email.header import Header
from pathlib import Path

from loguru import logger

from family_spending.ingestion.imap_163 import (
    HEADER_QUERY,
    RAW_QUERY,
    build_email_filename,
    encode_mailbox_name,
    fetch_raw_emails,
    parse_since_date,
    save_raw_email,
)
from family_spending.settings import (
    EmailCredentials,
    Imap163Settings,
    load_email_credentials,
)


class FakeImap:
    """Record protocol calls so behavior is verified without network access."""

    def __init__(
        self,
        headers: dict[bytes, bytes],
        raw_messages: dict[bytes, bytes],
        search_ids: bytes,
    ) -> None:
        self.headers = headers
        self.raw_messages = raw_messages
        self.search_ids = search_ids
        self.calls: list[tuple[object, ...]] = []
        self.fetch_calls: list[tuple[bytes, str]] = []

    def login(self, address: str, auth_code: str):
        self.calls.append(("login", address, auth_code))
        return "OK", [b"logged in"]

    def _simple_command(self, command: str, payload: str):
        self.calls.append(("id", command, payload))
        return "OK", [b"id accepted"]

    def select(self, mailbox: str, readonly: bool = False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def search(self, charset, criterion: str, since: str):
        self.calls.append(("search", charset, criterion, since))
        return "OK", [self.search_ids]

    def fetch(self, mail_id: bytes, query: str):
        self.fetch_calls.append((mail_id, query))
        payload = self.headers.get(mail_id) if query == HEADER_QUERY else self.raw_messages.get(mail_id)

        if payload is None:
            return "NO", [b"missing"]

        return "OK", [(b"metadata", payload), b")"]

    def logout(self):
        self.calls.append(("logout",))
        return "BYE", [b"logged out"]


def make_header(
    *,
    subject: str = "招商银行信用卡电子账单",
    message_id: str = "message@example.test",
    mail_date: str = "Fri, 10 Jul 2026 08:00:00 +0800",
) -> bytes:
    # Encoded Chinese keeps the fixture close to real mailbox headers.
    encoded_subject = Header(subject, "utf-8").encode()

    return (
        f"Subject: {encoded_subject}\r\n"
        f"Date: {mail_date}\r\n"
        f"Message-ID: <{message_id}>\r\n"
        "\r\n"
    ).encode("ascii")


class SettingsTests(unittest.TestCase):
    def test_credentials_only_use_private_environment_values(self) -> None:
        credentials = load_email_credentials(
            {
                "EMAIL_ADDR": "user@example.test",
                "EMAIL_AUTH_CODE": "fake-auth-code",
            }
        )

        self.assertEqual(credentials.address, "user@example.test")
        self.assertEqual(credentials.auth_code, "fake-auth-code")

    def test_missing_credentials_are_reported(self) -> None:
        with self.assertRaisesRegex(ValueError, "EMAIL_ADDR"):
            load_email_credentials({"EMAIL_AUTH_CODE": "fake-auth-code"})

        with self.assertRaisesRegex(ValueError, "EMAIL_AUTH_CODE"):
            load_email_credentials({"EMAIL_ADDR": "user@example.test"})


class Imap163Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Unit tests verify behavior; real-run logs are reviewed separately.
        logger.disable("family_spending.ingestion.imap_163")

    @classmethod
    def tearDownClass(cls) -> None:
        logger.enable("family_spending.ingestion.imap_163")

    def make_settings(self, output_dir: Path) -> Imap163Settings:
        return Imap163Settings(
            host="imap.example.test",
            port=993,
            mailbox="招行信用卡",
            subject_keyword="招商银行信用卡电子账单",
            since="01-Sep-2025",
            output_dir=output_dir,
        )

    def test_since_date_uses_imap_format(self) -> None:
        self.assertEqual(parse_since_date("01-Sep-2025"), date(2025, 9, 1))

    def test_chinese_mailbox_uses_modified_utf7(self) -> None:
        # This protects the encoding 163 needs for the real folder name.
        self.assertEqual(encode_mailbox_name("招行信用卡"), "&YtuITE,hdShTYQ-")

    def test_message_id_generates_stable_filename(self) -> None:
        first = build_email_filename(date(2026, 7, 10), " <same@example.test> ")
        second = build_email_filename(date(2026, 7, 10), "same@example.test")
        self.assertEqual(first, second)

    def test_missing_message_id_uses_raw_bytes_hash(self) -> None:
        raw_message = b"Subject: fake\r\n\r\nbody\r\n"
        filename = build_email_filename(date(2026, 7, 10), None, raw_message)

        self.assertIn(
            hashlib.sha256(raw_message).hexdigest()[:24],
            filename,
        )

    def test_raw_email_is_saved_exactly_once(self) -> None:
        raw_message = b"Subject: fake\r\nX-Test: \xff\r\n\r\nbody\x00\r\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "message.eml"

            self.assertTrue(save_raw_email(path, raw_message))
            self.assertFalse(save_raw_email(path, b"replacement"))

            # Raw bytes remain the source of truth and must not be normalized.
            self.assertEqual(path.read_bytes(), raw_message)

    def test_complete_flow_saves_once_and_skips_duplicate(self) -> None:
        raw_message = b"Subject: statement\r\n\r\nbody\r\n"
        fake = FakeImap(
            headers={
                b"1": make_header(message_id="same@example.test"),
                b"2": make_header(subject="unrelated"),
                b"3": make_header(message_id="same@example.test"),
            },
            raw_messages={b"1": raw_message},
            search_ids=b"1 2 3",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            summary = fetch_raw_emails(
                EmailCredentials(
                    address="user@example.test",
                    auth_code="fake-auth-code",
                ),
                self.make_settings(output_dir),
                imap_factory=lambda host, port: fake,
            )
            saved_files = list(output_dir.glob("*.eml"))

        self.assertEqual(summary.candidates, 3)
        self.assertEqual(summary.matched, 2)
        self.assertEqual(summary.saved, 1)
        self.assertEqual(summary.existing, 1)
        self.assertEqual(len(saved_files), 1)

        # Duplicate Message-ID avoids another complete MIME download.
        self.assertEqual(fake.fetch_calls.count((b"1", RAW_QUERY)), 1)
        self.assertNotIn((b"3", RAW_QUERY), fake.fetch_calls)

        id_index = next(index for index, call in enumerate(fake.calls) if call[0] == "id")
        select_index = next(
            index for index, call in enumerate(fake.calls) if call[0] == "select"
        )

        # 163 requires ID before selecting the mailbox.
        self.assertLess(id_index, select_index)

        # Read-only selection prevents changes to mailbox state.
        self.assertTrue(fake.calls[select_index][2])

    def test_fetch_failure_stops_processing(self) -> None:
        fake = FakeImap(
            headers={
                b"1": make_header(message_id="first@example.test"),
                b"2": make_header(message_id="second@example.test"),
            },
            raw_messages={b"2": b"second"},
            search_ids=b"1 2",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "message 1"):
                fetch_raw_emails(
                    EmailCredentials(
                        address="user@example.test",
                        auth_code="fake-auth-code",
                    ),
                    self.make_settings(Path(temp_dir)),
                    imap_factory=lambda host, port: fake,
                )

            self.assertEqual(list(Path(temp_dir).glob("*.eml")), [])

        # Fail fast so a broken fetch is not hidden by later messages.
        self.assertNotIn((b"2", RAW_QUERY), fake.fetch_calls)
        self.assertIn(("logout",), fake.calls)


if __name__ == "__main__":
    unittest.main()