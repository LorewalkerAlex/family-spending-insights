import os
import re
import imaplib
import email
from email import policy
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime
from typing import Iterable

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from imapclient import imap_utf7


load_dotenv()


IMAP_HOST = os.getenv("IMAP_HOST", "imap.163.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))

EMAIL_ADDR = os.getenv("EMAIL_ADDR")
EMAIL_AUTH_CODE = os.getenv("EMAIL_AUTH_CODE")

SINCE = os.getenv("SINCE", "01-Jan-2023")
MAILBOXES = os.getenv("MAILBOXES", "INBOX")
OUT_DIR = Path(os.getenv("OUT_DIR", "cmb_bill_output"))

KEYWORDS = [
    item.strip()
    for item in os.getenv("KEYWORDS", "").split(",")
    if item.strip()
]

RAW_COLUMNS = [
    "transaction_mmdd",
    "post_mmdd",
    "description",
    "raw_amount_text",
    "raw_amount",
    "card_last4",
    "country_or_region",
    "cny_amount_text",
    "cny_amount",
]


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_amount(value):
    if value is None:
        return None

    text = normalize_text(value)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&yen;", "¥")
    text = text.replace("￥", "¥")
    text = text.replace("¥", "")
    text = text.replace(",", "")
    text = text.strip()

    try:
        return float(text)
    except ValueError:
        return None


def decode_header_value(value) -> str:
    return "" if value is None else str(value)


def extract_first_tuple_payload(data):
    if not data:
        return None

    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            return item[1]

    return None


def match_keywords(subject: str, sender: str) -> bool:
    text = f"{subject} {sender}".lower()
    return any(keyword.lower() in text for keyword in KEYWORDS)


def parse_mail_date_to_ymd(mail_date: str) -> str:
    """
    用邮件 Date header 作为账单文件日期。
    例如：Wed, 10 Jun 2026 13:01:19 +0800 -> 2026-06-10
    """
    try:
        dt = parsedate_to_datetime(mail_date)
        return dt.date().isoformat()
    except Exception as exc:
        raise RuntimeError(f"Cannot parse mail date: {mail_date!r}") from exc


def extract_bodies(msg: EmailMessage) -> tuple[str, str]:
    html_parts = []
    text_parts = []

    parts: Iterable[EmailMessage] = msg.walk() if msg.is_multipart() else [msg]

    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue

        content_type = part.get_content_type()

        if content_type == "text/html":
            try:
                html_parts.append(part.get_content())
            except Exception:
                payload = part.get_payload(decode=True)
                if payload:
                    html_parts.append(payload.decode("utf-8", errors="ignore"))

        elif content_type == "text/plain":
            try:
                text_parts.append(part.get_content())
            except Exception:
                payload = part.get_payload(decode=True)
                if payload:
                    text_parts.append(payload.decode("utf-8", errors="ignore"))

    return "\n".join(html_parts), "\n".join(text_parts)


def send_imap_id(mail: imaplib.IMAP4_SSL) -> None:
    """
    163/126 邮箱在 login 后、select 前需要发送 IMAP ID，
    否则可能出现 SELECT Unsafe Login。
    """
    imaplib.Commands["ID"] = ("AUTH",)

    client_id = (
        "name", "mail-billing-extract",
        "version", "1.0.0",
        "vendor", "local-python-script",
        "contact", EMAIL_ADDR or "unknown",
    )
    payload = '("' + '" "'.join(client_id) + '")'

    try:
        mail._simple_command("ID", payload)
    except Exception as exc:
        log(f"warning: IMAP ID command failed: {exc}")


def encode_mailbox_name(mailbox: str) -> str:
    try:
        mailbox.encode("ascii")
        return mailbox
    except UnicodeEncodeError:
        return imap_utf7.encode(mailbox).decode("ascii")


def decode_mailbox_name(raw_name: str) -> str:
    try:
        return imap_utf7.decode(raw_name.encode("ascii"))
    except Exception:
        return raw_name


def parse_mailbox_names(mail: imaplib.IMAP4_SSL) -> list[str]:
    status, data = mail.list()
    if status != "OK" or not data:
        return ["INBOX"]

    mailboxes = []

    for raw in data:
        if not raw:
            continue

        line = raw.decode("ascii", errors="ignore")
        match = re.search(r'"([^"]+)"\s*$', line)

        if match:
            name = match.group(1)
        else:
            parts = line.split()
            name = parts[-1].strip('"') if parts else ""

        if name:
            mailboxes.append(name)

    if "INBOX" not in mailboxes:
        mailboxes.insert(0, "INBOX")

    return list(dict.fromkeys(mailboxes))


def get_target_mailboxes(mail: imaplib.IMAP4_SSL) -> list[str]:
    value = MAILBOXES.strip()

    if value.upper() == "ALL":
        return parse_mailbox_names(mail)

    mailboxes = [item.strip() for item in value.split(",") if item.strip()]
    return mailboxes or ["INBOX"]


def select_mailbox(mail: imaplib.IMAP4_SSL, mailbox: str) -> tuple[str, list | None, str]:
    encoded = encode_mailbox_name(mailbox)
    attempts = [encoded, f'"{encoded}"']

    last_status = "NO"
    last_data = None
    last_attempt = encoded

    for attempt in dict.fromkeys(attempts):
        status, data = mail.select(attempt)
        if status == "OK":
            return status, data, attempt

        last_status = status
        last_data = data
        last_attempt = attempt

    return last_status, last_data, last_attempt


def fetch_header(mail: imaplib.IMAP4_SSL, mail_id: bytes):
    status, data = mail.fetch(
        mail_id,
        "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])",
    )
    if status != "OK":
        return None

    raw_header = extract_first_tuple_payload(data)
    if not raw_header:
        return None

    return email.message_from_bytes(raw_header, policy=policy.default)


def fetch_full_message(mail: imaplib.IMAP4_SSL, mail_id: bytes):
    status, data = mail.fetch(mail_id, "(RFC822)")
    if status != "OK":
        return None

    raw_msg = extract_first_tuple_payload(data)
    if not raw_msg:
        return None

    return email.message_from_bytes(raw_msg, policy=policy.default)


def extract_cmb_transaction_rows_from_html(html: str) -> pd.DataFrame:
    """
    只做原始交易明细抽取，不做校验、聚合、打标或日期补全年份。

    当前招行账单模板中，每条交易通常位于：
    TABLE width=643 height=18，且一行包含 8 个 TD：
    ['', transaction_mmdd, post_mmdd, description, raw_amount, card_last4, country, cny_amount]
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for table in soup.find_all("table"):
        width = str(table.get("width", "")).strip()
        height = str(table.get("height", "")).strip()

        if width != "643" or height != "18":
            continue

        tr = table.find("tr")
        if tr is None:
            continue

        cells = [
            normalize_text(td.get_text(" ", strip=True))
            for td in tr.find_all("td", recursive=False)
        ]

        if len(cells) != 8:
            continue

        _, transaction_mmdd, post_mmdd, description, raw_amount_text, card_last4, country, cny_amount_text = cells

        if not re.fullmatch(r"\d{4}", transaction_mmdd):
            continue
        if not re.fullmatch(r"\d{4}", post_mmdd):
            continue
        if not description:
            continue

        raw_amount = parse_amount(raw_amount_text)
        cny_amount = parse_amount(cny_amount_text)

        if raw_amount is None and cny_amount is None:
            continue

        rows.append(
            {
                "transaction_mmdd": transaction_mmdd,
                "post_mmdd": post_mmdd,
                "description": description,
                "raw_amount_text": raw_amount_text,
                "raw_amount": raw_amount,
                "card_last4": card_last4,
                "country_or_region": country,
                "cny_amount_text": cny_amount_text,
                "cny_amount": cny_amount,
            }
        )

    if not rows:
        return pd.DataFrame(columns=RAW_COLUMNS)

    return pd.DataFrame(rows)[RAW_COLUMNS]


def extract_one_mail(mail: imaplib.IMAP4_SSL, mail_id: bytes, output_file: Path) -> int:
    msg = fetch_full_message(mail, mail_id)
    if msg is None:
        return 0

    html, _ = extract_bodies(msg)
    if not html:
        return 0

    df = extract_cmb_transaction_rows_from_html(html)
    if df.empty:
        return 0

    output_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_file, index=False, encoding="utf-8-sig")
    return len(df)


def main() -> None:
    if not EMAIL_ADDR:
        raise RuntimeError("Missing EMAIL_ADDR in .env")
    if not EMAIL_AUTH_CODE:
        raise RuntimeError("Missing EMAIL_AUTH_CODE in .env")
    if not KEYWORDS:
        raise RuntimeError("Missing KEYWORDS in .env")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    scanned_count = 0
    matched_count = 0
    extracted_count = 0
    skipped_existing_count = 0
    row_count = 0

    log(f"since={SINCE}; mailboxes={MAILBOXES}; out_dir={OUT_DIR}")

    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as mail:
        login_status, login_data = mail.login(EMAIL_ADDR, EMAIL_AUTH_CODE)
        if login_status != "OK":
            raise RuntimeError(f"IMAP login failed: {login_status}, {login_data}")

        send_imap_id(mail)

        for mailbox in get_target_mailboxes(mail):
            status, data, selected_name = select_mailbox(mail, mailbox)
            display_name = decode_mailbox_name(selected_name.strip('"'))

            if status != "OK":
                log(f"skip mailbox={mailbox}: select failed: {data}")
                continue

            status, data = mail.search(None, "SINCE", SINCE)
            if status != "OK":
                log(f"skip mailbox={display_name}: search failed: {data}")
                continue

            mail_ids = data[0].split() if data and data[0] else []
            log(f"mailbox={display_name}: found={len(mail_ids)}")

            for mail_id in mail_ids:
                scanned_count += 1

                header = fetch_header(mail, mail_id)
                if header is None:
                    continue

                subject = decode_header_value(header.get("Subject"))
                sender = decode_header_value(header.get("From"))
                mail_date = decode_header_value(header.get("Date"))

                if not match_keywords(subject, sender):
                    continue

                matched_count += 1

                bill_ymd = parse_mail_date_to_ymd(mail_date)
                output_file = OUT_DIR / f"{bill_ymd}-cmbbilling-raw.csv"

                if output_file.exists():
                    skipped_existing_count += 1
                    log(f"skip existing: {output_file.name}")
                    continue

                rows = extract_one_mail(mail, mail_id, output_file)
                if rows > 0:
                    extracted_count += 1
                    row_count += rows
                    log(f"extracted: {output_file.name}, rows={rows}")
                else:
                    log(f"matched but no rows: subject={subject!r}, date={mail_date!r}")

        mail.logout()

    log(
        "done: "
        f"scanned={scanned_count}, "
        f"matched={matched_count}, "
        f"extracted={extracted_count}, "
        f"skipped_existing={skipped_existing_count}, "
        f"rows={row_count}"
    )


if __name__ == "__main__":
    main()
