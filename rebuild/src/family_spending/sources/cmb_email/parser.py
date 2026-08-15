from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

from bs4 import BeautifulSoup

from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence

CMB_SOURCE_TYPE = "cmb_email"
CMB_CURRENCY = "CNY"
MMDD_RE = re.compile(r"\d{4}")
TARGET_WIDTH = "643"
TARGET_HEIGHT = "18"


class CmbEmailParseError(RuntimeError):
    """Raised when one immutable CMB EML cannot be normalized safely."""


@dataclass(frozen=True)
class ParsedCmbEmail:
    """Normalized records plus diagnostics that do not participate in identity."""

    records: tuple[SourceRecord, ...]
    skipped_repayments: int


def normalize_text(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()


def parse_amount(value: object | None) -> Decimal:
    """Parse the statement amount exactly without float conversion or silent coercion."""
    text = normalize_text(value)
    cleaned = (
        text.replace("&yen;", "")
        .replace("\u00a5", "")
        .replace("\uffe5", "")
        .replace(",", "")
        .replace(" ", "")
    )
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        raise CmbEmailParseError(f"Invalid CMB amount: {text!r}")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CmbEmailParseError(f"Invalid CMB amount: {text!r}") from exc
    if not amount.is_finite():
        raise CmbEmailParseError(f"Invalid CMB amount: {text!r}")
    return amount


def complete_mmdd(mmdd: str, email_date: date) -> date:
    """Complete statement MMDD using the email date as the year boundary."""
    if MMDD_RE.fullmatch(mmdd) is None:
        raise CmbEmailParseError(f"Invalid MMDD value: {mmdd!r}")
    month = int(mmdd[:2])
    day = int(mmdd[2:])
    year = email_date.year - 1 if month > email_date.month else email_date.year
    try:
        completed = date(year, month, day)
    except ValueError as exc:
        raise CmbEmailParseError(f"Invalid MMDD calendar date: {mmdd!r}") from exc
    if completed > email_date:
        raise CmbEmailParseError(
            f"Completed date {completed} is later than email date {email_date}"
        )
    return completed


def _parse_email_date(message: EmailMessage, evidence: CmbEmailEvidence) -> date:
    header = message.get("Date")
    if header is None:
        raise CmbEmailParseError(f"Missing Date header in {evidence.identity}")
    try:
        parsed = parsedate_to_datetime(str(header))
    except (TypeError, ValueError, OverflowError) as exc:
        raise CmbEmailParseError(f"Invalid Date header in {evidence.identity}") from exc
    if parsed is None:
        raise CmbEmailParseError(f"Invalid Date header in {evidence.identity}")
    return parsed.date()


def _decode_html_part(
    part: EmailMessage,
    evidence: CmbEmailEvidence,
    part_index: int,
) -> str:
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        raise CmbEmailParseError(
            f"Unable to read HTML part {part_index} in {evidence.identity}"
        )
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset)
    except (LookupError, UnicodeDecodeError) as exc:
        raise CmbEmailParseError(
            f"Unable to decode HTML part {part_index} in {evidence.identity} "
            f"with charset {charset!r}"
        ) from exc


def _html_part_key(html: str, duplicate_ordinal: int) -> str:
    """Identify a MIME HTML body by content, not by current parser walk position."""
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
    return f"html:{digest}:{duplicate_ordinal}"


def _parse_html(
    evidence: CmbEmailEvidence,
    html: str,
    email_date: date,
    part_key: str,
) -> ParsedCmbEmail:
    soup = BeautifulSoup(html, "html.parser")
    records: list[SourceRecord] = []
    skipped_repayments = 0

    # The locator uses the absolute DOM table ordinal, including tables this parser
    # version ignores. Expanding recognition later therefore cannot renumber an
    # already recognized row merely because another row begins producing a record.
    for table_ordinal, table in enumerate(soup.find_all("table"), start=1):
        if (
            normalize_text(table.get("width")) != TARGET_WIDTH
            or normalize_text(table.get("height")) != TARGET_HEIGHT
        ):
            continue

        row = table.find("tr")
        if row is None:
            continue
        cells = [
            normalize_text(cell.get_text(" ", strip=True))
            for cell in row.find_all("td", recursive=False)
        ]
        if len(cells) != 8:
            continue

        _, transaction_mmdd, post_mmdd, description, amount_text, _, _, _ = cells
        if not transaction_mmdd:
            if MMDD_RE.fullmatch(post_mmdd) and description and "\u8fd8\u6b3e" in description:
                parse_amount(amount_text)
                skipped_repayments += 1
                continue
            raise CmbEmailParseError(
                f"Unexpected date-less row in {evidence.identity}: {description!r}"
            )
        if MMDD_RE.fullmatch(transaction_mmdd) is None:
            raise CmbEmailParseError(
                f"Invalid transaction date in {evidence.identity}: {transaction_mmdd!r}"
            )
        if MMDD_RE.fullmatch(post_mmdd) is None:
            raise CmbEmailParseError(
                f"Invalid post date in {evidence.identity}: {post_mmdd!r}"
            )
        if not description:
            raise CmbEmailParseError(f"Empty description in {evidence.identity}")

        identity = SourceIdentity(
            source_type=CMB_SOURCE_TYPE,
            evidence_identity=evidence.identity,
            record_locator=f"{part_key}/table:{table_ordinal}",
        )
        records.append(
            SourceRecord(
                identity=identity,
                transaction_type="expense",
                transaction_date=complete_mmdd(transaction_mmdd, email_date),
                amount=parse_amount(amount_text),
                currency=CMB_CURRENCY,
                description=description,
            )
        )

    return ParsedCmbEmail(records=tuple(records), skipped_repayments=skipped_repayments)


def parse_cmb_email(evidence: CmbEmailEvidence) -> ParsedCmbEmail:
    """Normalize one raw EML while keeping SourceRecord identity evidence-anchored."""
    message = message_from_bytes(evidence.raw_bytes, policy=policy.default)
    email_date = _parse_email_date(message, evidence)
    candidates: list[ParsedCmbEmail] = []
    html_digest_counts: defaultdict[str, int] = defaultdict(int)
    parts = message.walk() if message.is_multipart() else (message,)

    for part_index, part in enumerate(parts, start=1):
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() != "text/html":
            continue

        html = _decode_html_part(part, evidence, part_index)
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        html_digest_counts[digest] += 1
        part_key = _html_part_key(html, html_digest_counts[digest])
        parsed = _parse_html(evidence, html, email_date, part_key)
        if parsed.records or parsed.skipped_repayments:
            candidates.append(parsed)

    if not candidates:
        raise CmbEmailParseError(f"No CMB transaction table found in {evidence.identity}")
    if len(candidates) != 1:
        raise CmbEmailParseError(
            f"Multiple CMB transaction HTML parts found in {evidence.identity}"
        )
    if not candidates[0].records:
        raise CmbEmailParseError(f"No CMB transactions found in {evidence.identity}")
    return candidates[0]
