from __future__ import annotations

import hashlib
import imaplib
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from email import policy
from email.parser import BytesHeaderParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from imapclient import imap_utf7
from loguru import logger

HEADER_QUERY = "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])"
RAW_QUERY = "(BODY.PEEK[])"
HASH_PREFIX_LENGTH = 24


@dataclass(frozen=True)
class Imap163Config:
    """Keep runtime settings explicit so imports never access the mailbox."""

    host: str
    port: int
    email_addr: str
    auth_code: str
    since: str
    since_date: date
    mailboxes: tuple[str, ...] | None
    keywords: tuple[str, ...]
    out_dir: Path


@dataclass
class FetchSummary:
    """Keep only counters needed to verify one ingestion run."""

    mailboxes: int = 0
    candidates: int = 0
    headers: int = 0
    date_skipped: int = 0
    keyword_skipped: int = 0
    matched: int = 0
    saved: int = 0
    existing: int = 0
    failed: int = 0


def parse_since_date(value: str) -> date:
    """Reject ambiguous dates before server and local filters use them."""

    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid SINCE value {value!r}; expected DD-Mon-YYYY"
        ) from exc


def match_keywords(subject: str, sender: str, keywords: tuple[str, ...]) -> bool:
    """Apply one local case-insensitive OR rule independent of IMAP quirks."""

    text = f"{subject} {sender}".casefold()
    return any(keyword.casefold() in text for keyword in keywords)


def encode_mailbox_name(mailbox: str) -> str:
    """Encode only non-ASCII folders because ASCII names are already valid."""

    try:
        mailbox.encode("ascii")
        return mailbox
    except UnicodeEncodeError:
        return imap_utf7.encode(mailbox).decode("ascii")


def _normalize_message_id(message_id: str | None) -> str:
    """Remove transport formatting so folder copies share one identity."""

    if not message_id:
        return ""
    return "".join(message_id.split()).strip("<>")


def build_email_filename(
    mail_date: date,
    message_id: str | None,
    raw_message: bytes | None = None,
) -> str:
    """Prefer Message-ID so duplicates can be skipped before MIME download."""

    normalized_id = _normalize_message_id(message_id)
    if normalized_id:
        identity = normalized_id.encode("utf-8")
    elif raw_message is not None:
        identity = raw_message
    else:
        raise ValueError("raw_message is required when Message-ID is missing")
    digest = hashlib.sha256(identity).hexdigest()[:HASH_PREFIX_LENGTH]
    return f"{mail_date.isoformat()}_{digest}.eml"


def save_raw_email(path: Path, raw_message: bytes) -> bool:
    """Use a temporary file so interruption cannot leave a partial email."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    temp_path: Path | None = None
    try:
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


def load_config(environ: Mapping[str, str] | None = None) -> Imap163Config:
    """Read .env only at the executable boundary to keep imports deterministic."""

    if environ is None:
        load_dotenv()
        environ = os.environ
    email_addr = environ.get("EMAIL_ADDR", "").strip()
    auth_code = environ.get("EMAIL_AUTH_CODE", "").strip()
    keywords = tuple(
        value.strip()
        for value in environ.get("KEYWORDS", "").split(",")
        if value.strip()
    )
    if not email_addr:
        raise ValueError("Missing required environment variable: EMAIL_ADDR")
    if not auth_code:
        raise ValueError("Missing required environment variable: EMAIL_AUTH_CODE")
    if not keywords:
        raise ValueError("Missing required environment variable: KEYWORDS")
    port_text = environ.get("IMAP_PORT", "993").strip() or "993"
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"Invalid IMAP_PORT value {port_text!r}") from exc

    since = environ.get("SINCE", "01-Jan-2023").strip() or "01-Jan-2023"
    mailbox_text = environ.get("MAILBOXES", "INBOX").strip() or "INBOX"
    mailboxes = None
    if mailbox_text.casefold() != "all":
        mailboxes = tuple(
            name.strip() for name in mailbox_text.split(",") if name.strip()
        )

    out_dir = (
        environ.get("CMB_EMAIL_DIR", "").strip()
        or environ.get("OUT_DIR", "").strip()
        or "data/emails/163/cmb"
    )
    return Imap163Config(
        host=environ.get("IMAP_HOST", "imap.163.com").strip() or "imap.163.com",
        port=port,
        email_addr=email_addr,
        auth_code=auth_code,
        since=since,
        since_date=parse_since_date(since),
        mailboxes=mailboxes
        or (("INBOX",) if mailbox_text.casefold() != "all" else None),
        keywords=keywords,
        out_dir=Path(out_dir),
    )


def _fetch_bytes(mail: Any, mail_id: bytes, query: str) -> bytes:
    """Fail closed so incomplete server responses are never persisted."""

    status, data = mail.fetch(mail_id, query)
    if status == "OK" and data:
        for item in data:
            if (
                isinstance(item, tuple)
                and len(item) >= 2
                and isinstance(item[1], bytes)
            ):
                return item[1]

    raise RuntimeError(f"fetch failed: status={status!r}, data={data!r}")


def _send_imap_id(mail: Any) -> None:
    """Send ID before folder selection because 163 requires this handshake."""

    imaplib.Commands["ID"] = ("AUTH",)
    payload = (
        '("name" "family-spending-insights" '
        '"version" "0.1.0" '
        '"vendor" "local-python-script")'
    )

    status, data = mail._simple_command("ID", payload)
    if status != "OK":
        raise RuntimeError(f"IMAP ID command failed: {data!r}")


def _parse_list_mailbox(raw_line: bytes) -> str:
    """Read the final LIST token because flags and delimiters may vary."""

    line = raw_line.decode("ascii").strip()
    match = re.search(r'(?:(?:"((?:\\.|[^"\\])*)")|(\S+))\s*$', line)

    if not match:
        raise ValueError(f"Unable to parse mailbox LIST response: {line!r}")

    mailbox = match.group(1) if match.group(1) is not None else match.group(2)
    return re.sub(r"\\(.)", r"\1", mailbox)


def _mailbox_targets(
    mail: Any,
    configured_mailboxes: tuple[str, ...] | None,
) -> list[tuple[str, str]]:
    """Call LIST only for ALL so configured runs remain predictable and cheap."""

    if configured_mailboxes is not None:
        return [(name, encode_mailbox_name(name)) for name in configured_mailboxes]

    status, data = mail.list()
    if status != "OK" or not data:
        raise RuntimeError(f"IMAP LIST failed: {data!r}")

    targets: list[tuple[str, str]] = []
    for raw_line in data:
        if not isinstance(raw_line, bytes) or not raw_line:
            continue

        encoded_name = _parse_list_mailbox(raw_line)

        try:
            display_name = imap_utf7.decode(encoded_name.encode("ascii"))
        except Exception:
            display_name = encoded_name

        targets.append((display_name, encoded_name))

    if not targets:
        raise RuntimeError("IMAP LIST returned no mailbox names")

    return list(dict.fromkeys(targets))


def _select_mailbox(mail: Any, encoded_name: str) -> str:
    """Try quoted and unquoted names because 163 handles folders inconsistently."""

    last_data: Any = None

    for candidate in dict.fromkeys((encoded_name, f'"{encoded_name}"')):
        status, data = mail.select(candidate, readonly=True)

        if status == "OK":
            return candidate

        last_data = data

    raise RuntimeError(f"select failed: {last_data!r}")


def _parse_header(raw_header: bytes) -> tuple[str, str, date | None, str | None]:
    """Parse only routing fields because raw MIME bytes must remain untouched."""

    header = BytesHeaderParser(policy=policy.default).parsebytes(raw_header)
    subject = str(header.get("Subject", ""))
    sender = str(header.get("From", ""))
    message_id = str(header["Message-ID"]) if header.get("Message-ID") else None
    try:
        mail_date = parsedate_to_datetime(str(header.get("Date", ""))).date()
    except (TypeError, ValueError, OverflowError):
        mail_date = None

    return subject, sender, mail_date, message_id


def fetch_raw_emails(
    config: Imap163Config,
    *,
    imap_factory: Callable[[str, int], Any] = imaplib.IMAP4_SSL,
) -> FetchSummary:
    """Fetch matching raw emails while logging only useful operational milestones."""

    summary = FetchSummary()
    config.out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Configuration loaded: host={}, port={}, since={}, mailboxes={}, "
        "keyword_count={}, out_dir={}",
        config.host,
        config.port,
        config.since,
        "ALL" if config.mailboxes is None else list(config.mailboxes),
        len(config.keywords),
        config.out_dir,
    )
    logger.info(
        "Connecting to IMAP server: host={}, port={}",
        config.host,
        config.port,
    )
    mail = imap_factory(config.host, config.port)
    logged_in = False

    try:
        status, data = mail.login(config.email_addr, config.auth_code)

        if status != "OK":
            raise RuntimeError(f"IMAP login failed: {data!r}")

        logged_in = True
        logger.info("IMAP login succeeded")

        _send_imap_id(mail)
        logger.info("IMAP ID command succeeded")
        for display_name, encoded_name in _mailbox_targets(mail, config.mailboxes):
            try:
                selected_name = _select_mailbox(mail, encoded_name)
                status, data = mail.search(None, "SENTSINCE", config.since)

                if status != "OK":
                    raise RuntimeError(f"search failed: {data!r}")
                summary.mailboxes += 1
            except Exception as exc:
                summary.failed += 1
                logger.exception(
                    "Mailbox failed: name={}, error={}",
                    display_name,
                    exc,
                )
                continue

            mail_ids = data[0].split() if data and data[0] else []
            summary.candidates += len(mail_ids)
            logger.info(
                "Mailbox search completed: name={}, encoded={}, candidates={}",
                display_name,
                selected_name,
                len(mail_ids),
            )

            for mail_id in mail_ids:
                message_no = mail_id.decode("ascii", errors="replace")
                try:
                    # BODY.PEEK prevents reads from marking the message as read.
                    raw_header = _fetch_bytes(mail, mail_id, HEADER_QUERY)
                    summary.headers += 1

                    subject, sender, mail_date, message_id = _parse_header(
                        raw_header
                    )
                    # SENTSINCE narrows results, but the header remains authoritative.
                    if mail_date is None or mail_date < config.since_date:
                        summary.date_skipped += 1
                        continue

                    if not match_keywords(subject, sender, config.keywords):
                        summary.keyword_skipped += 1
                        continue
                    summary.matched += 1
                    filename = None
                    output_path = None

                    if _normalize_message_id(message_id):
                        # Message-ID avoids downloading the same MIME data again.
                        filename = build_email_filename(mail_date, message_id)
                        output_path = config.out_dir / filename
                        if output_path.exists():
                            summary.existing += 1
                            logger.info(
                                "Raw email already exists: mailbox={}, file={}",
                                display_name,
                                filename,
                            )
                            continue

                    raw_message = _fetch_bytes(mail, mail_id, RAW_QUERY)
                    if filename is None or output_path is None:
                        filename = build_email_filename(
                            mail_date,
                            None,
                            raw_message,
                        )
                        output_path = config.out_dir / filename
                    if save_raw_email(output_path, raw_message):
                        summary.saved += 1
                        logger.info(
                            "Raw email saved: mailbox={}, message={}, "
                            "bytes={}, file={}",
                            display_name,
                            message_no,
                            len(raw_message),
                            filename,
                        )
                    else:
                        summary.existing += 1
                        logger.info(
                            "Raw email already exists: mailbox={}, file={}",
                            display_name,
                            filename,
                        )
                except Exception as exc:
                    summary.failed += 1
                    logger.exception(
                        "Message failed: mailbox={}, message={}, error={}",
                        display_name,
                        message_no,
                        exc,
                    )
            logger.info(
                "Mailbox completed: name={}, candidates={}",
                display_name,
                len(mail_ids),
            )
    finally:
        if logged_in:
            try:
                mail.logout()
                logger.info("IMAP logout succeeded")
            except Exception as exc:
                summary.failed += 1
                logger.exception("IMAP logout failed: error={}", exc)
    logger.info(
        "Ingestion completed: mailboxes={}, candidates={}, headers={}, "
        "date_skipped={}, keyword_skipped={}, matched={}, saved={}, "
        "existing={}, failed={}",
        summary.mailboxes,
        summary.candidates,
        summary.headers,
        summary.date_skipped,
        summary.keyword_skipped,
        summary.matched,
        summary.saved,
        summary.existing,
        summary.failed,
    )

    return summary


def configure_logging() -> None:
    """Write to stdout so PowerShell does not report normal logs as errors."""

    logger.remove()
    logger.add(
        sys.stdout,
        level="DEBUG",
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<level>{message}</level>"
        ),
        colorize=sys.stdout.isatty(),
        backtrace=False,
        diagnose=False,
    )


def main() -> None:
    """Keep configuration loading and side effects at the executable boundary."""

    configure_logging()
    logger.info("Starting 163 raw email ingestion")
    fetch_raw_emails(load_config())


if __name__ == "__main__":
    main()