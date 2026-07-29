from __future__ import annotations

import hashlib
import imaplib
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from email import policy
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from imapclient import imap_utf7
from loguru import logger

from family_spending.settings import (
    IMAP_163,
    EmailCredentials,
    Imap163Settings,
    load_email_credentials,
)

# PEEK keeps the user's mailbox read/unread state unchanged.
HEADER_QUERY = "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE MESSAGE-ID)])"
RAW_QUERY = "(BODY.PEEK[])"
HASH_PREFIX_LENGTH = 24


@dataclass(frozen=True)
class EmailHeader:
    mail_date: date
    message_id: str | None


@dataclass(frozen=True)
class MatchedEmail:
    mail_id: bytes
    header: EmailHeader


@dataclass(frozen=True)
class FetchSummary:
    candidates: int
    matched: int
    saved: int
    existing: int


def parse_since_date(value: str) -> date:
    """Reject an invalid date before opening a network connection."""
    try:
        return datetime.strptime(value, "%d-%b-%Y").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid IMAP since value {value!r}; expected DD-Mon-YYYY"
        ) from exc


def encode_mailbox_name(mailbox: str) -> str:
    """Use Modified UTF-7 because IMAP mailbox names are not normal UTF-8."""
    try:
        mailbox.encode("ascii")
        return mailbox
    except UnicodeEncodeError:
        return imap_utf7.encode(mailbox).decode("ascii")


def _normalize_message_id(message_id: str | None) -> str:
    if not message_id:
        return ""

    # Transport formatting is not part of the message identity.
    return "".join(message_id.split()).strip("<>")


def build_email_filename(
    mail_date: date,
    message_id: str | None,
    raw_message: bytes | None = None,
) -> str:
    normalized_id = _normalize_message_id(message_id)

    if normalized_id:
        # Message-ID lets reruns skip the full MIME download.
        identity = normalized_id.encode("utf-8")
    elif raw_message is not None:
        # Raw bytes keep emails without Message-ID deterministic and unique.
        identity = raw_message
    else:
        raise ValueError("raw_message is required when Message-ID is missing")

    digest = hashlib.sha256(identity).hexdigest()[:HASH_PREFIX_LENGTH]
    return f"{mail_date.isoformat()}_{digest}.eml"


def save_raw_email(path: Path, raw_message: bytes) -> bool:
    """Save immutable source bytes without replacing an existing email."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        return False

    temp_path: Path | None = None

    try:
        # Atomic replacement prevents interrupted writes from leaving partial .eml files.
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(raw_message)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)

        if path.exists():
            return False

        os.replace(temp_path, path)
        temp_path = None
        return True
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _fetch_bytes(mail: Any, mail_id: bytes, query: str) -> bytes:
    status, data = mail.fetch(mail_id, query)

    if status == "OK" and data:
        for item in data:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                return item[1]

    message_no = mail_id.decode("ascii", errors="replace")
    raise RuntimeError(
        f"Failed to fetch message {message_no}: status={status!r}, data={data!r}"
    )


def _send_imap_id(mail: Any) -> None:
    """Send the client handshake before selection because 163 requires it."""
    imaplib.Commands["ID"] = ("AUTH",)
    payload = (
        '("name" "family-spending-insights" '
        '"version" "0.1.0" '
        '"vendor" "local-python-script")'
    )

    status, data = mail._simple_command("ID", payload)
    if status != "OK":
        raise RuntimeError(f"IMAP ID command failed: {data!r}")


def _select_mailbox(mail: Any, mailbox: str) -> None:
    encoded_mailbox = encode_mailbox_name(mailbox)
    last_data: Any = None

    # 163 has returned different results for quoted and unquoted folder names.
    for candidate in dict.fromkeys((encoded_mailbox, f'"{encoded_mailbox}"')):
        status, data = mail.select(candidate, readonly=True)

        if status == "OK":
            return

        last_data = data

    raise RuntimeError(f"Failed to select mailbox {mailbox!r}: {last_data!r}")


def _search_message_ids(mail: Any, since: str) -> list[bytes]:
    # Server filtering reduces traffic; parsed dates are checked locally again.
    status, data = mail.search(None, "SENTSINCE", since)

    if status != "OK":
        raise RuntimeError(f"IMAP search failed: {data!r}")

    return data[0].split() if data and data[0] else []


def _parse_matching_header(
    mail_id: bytes,
    raw_header: bytes,
    subject_keyword: str,
) -> EmailHeader | None:
    header = BytesHeaderParser(policy=policy.default).parsebytes(raw_header)
    subject = str(header.get("Subject", ""))

    # Local matching avoids inconsistent Chinese text search on IMAP servers.
    if subject_keyword.casefold() not in subject.casefold():
        return None

    message_no = mail_id.decode("ascii", errors="replace")

    try:
        mail_date = parsedate_to_datetime(str(header.get("Date", ""))).date()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Invalid Date header in message {message_no}") from exc

    message_id = str(header["Message-ID"]) if header.get("Message-ID") else None
    return EmailHeader(mail_date=mail_date, message_id=message_id)


def _find_matching_emails(
    mail: Any,
    mail_ids: list[bytes],
    settings: Imap163Settings,
    since_date: date,
) -> list[MatchedEmail]:
    matches: list[MatchedEmail] = []

    for mail_id in mail_ids:
        raw_header = _fetch_bytes(mail, mail_id, HEADER_QUERY)
        header = _parse_matching_header(mail_id, raw_header, settings.subject_keyword)

        if header is None or header.mail_date < since_date:
            continue

        matches.append(MatchedEmail(mail_id=mail_id, header=header))

    return matches


def fetch_raw_emails(
    credentials: EmailCredentials,
    settings: Imap163Settings = IMAP_163,
    *,
    imap_factory: Callable[[str, int], Any] = imaplib.IMAP4_SSL,
) -> FetchSummary:
    """Fetch the single verified 163/CMB flow and fail fast on errors."""
    since_date = parse_since_date(settings.since)

    logger.info(
        "Starting 163 email ingestion: mailbox={}, since={}, output={}",
        settings.mailbox,
        settings.since,
        settings.output_dir,
    )

    mail = imap_factory(settings.host, settings.port)
    logged_in = False

    try:
        status, data = mail.login(credentials.address, credentials.auth_code)
        if status != "OK":
            raise RuntimeError(f"IMAP login failed: {data!r}")

        logged_in = True
        logger.info("IMAP login succeeded: host={}", settings.host)

        _send_imap_id(mail)

        _select_mailbox(mail, settings.mailbox)
        logger.info("Mailbox selected: {}", settings.mailbox)

        mail_ids = _search_message_ids(mail, settings.since)
        logger.info("Search completed: candidates={}", len(mail_ids))

        matches = _find_matching_emails(mail, mail_ids, settings, since_date)
        logger.info("Target emails matched: {}", len(matches))

        saved = 0
        existing = 0

        for index, matched_email in enumerate(matches, start=1):
            header = matched_email.header
            filename: str | None = None
            output_path: Path | None = None

            if _normalize_message_id(header.message_id):
                filename = build_email_filename(header.mail_date, header.message_id)
                output_path = settings.output_dir / filename

                if output_path.exists():
                    existing += 1
                    continue

            raw_message = _fetch_bytes(mail, matched_email.mail_id, RAW_QUERY)

            if filename is None or output_path is None:
                filename = build_email_filename(header.mail_date, None, raw_message)
                output_path = settings.output_dir / filename

            if save_raw_email(output_path, raw_message):
                saved += 1
                logger.info(
                    "Email saved: progress={}/{}, bytes={}, file={}",
                    index,
                    len(matches),
                    len(raw_message),
                    filename,
                )
            else:
                existing += 1
    finally:
        if logged_in:
            try:
                # Cleanup errors should not hide the original ingestion failure.
                mail.logout()
            except Exception as exc:
                logger.warning("IMAP logout failed: {}", exc)

    summary = FetchSummary(
        candidates=len(mail_ids),
        matched=len(matches),
        saved=saved,
        existing=existing,
    )

    logger.info(
        "163 email ingestion completed: matched={}, saved={}, existing={}",
        summary.matched,
        summary.saved,
        summary.existing,
    )
    return summary


def main() -> None:
    fetch_raw_emails(load_email_credentials())


if __name__ == "__main__":
    main()