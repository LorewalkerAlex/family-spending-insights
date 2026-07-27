import os
import re
import imaplib
import email
from collections import Counter
from datetime import datetime
from email import policy
from email.message import EmailMessage
from pathlib import Path
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

DEFAULT_KEYWORDS = [
    "招商",
    "招商银行",
    "招商银行信用卡",
    "招商银行信用卡电子账单",
    "信用卡",
    "信用卡账单",
    "电子账单",
    "账单",
    "CMB",
    "cmb",
    "cmbchina",
    "China Merchants",
    "statement",
    "bill",
    "credit card",
    "creditcard",
]
KEYWORDS = [
    x.strip()
    for x in os.getenv("KEYWORDS", ",".join(DEFAULT_KEYWORDS)).split(",")
    if x.strip()
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


def since_to_output_date(since: str) -> str:
    """Convert IMAP SINCE date, e.g. 01-Sep-2025, to YYYY-MM-DD."""
    try:
        return datetime.strptime(since, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return datetime.now().date().isoformat()


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
    163/126 mailboxes may require IMAP ID after login and before select,
    otherwise SELECT may fail with "Unsafe Login".
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
    return [x.strip() for x in value.split(",") if x.strip()] or ["INBOX"]


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
    Extract raw CMB credit-card transactions only.
    No validation, tagging, aggregation, date inference, or refund enrichment.

    Current CMB template transaction row:
    TABLE width=643 height=18 with 8 direct TD cells:
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


def row_counter(df: pd.DataFrame) -> Counter:
    if df.empty:
        return Counter()

    normalized = df[RAW_COLUMNS].fillna("")
    return Counter(
        tuple(str(row[col]) for col in RAW_COLUMNS)
        for row in normalized.to_dict(orient="records")
    )


def load_existing_output(output_file: Path) -> pd.DataFrame:
    if not output_file.exists():
        return pd.DataFrame(columns=RAW_COLUMNS)

    existing = pd.read_csv(output_file, dtype=str, encoding="utf-8-sig")
    missing = [col for col in RAW_COLUMNS if col not in existing.columns]
    if missing:
        raise RuntimeError(f"Existing output file misses required columns: {missing}")

    return existing[RAW_COLUMNS]


def already_extracted(mail_counter: Counter, existing_counter: Counter) -> bool:
    if not mail_counter:
        return False
    return all(existing_counter[row] >= count for row, count in mail_counter.items())


def main() -> None:
    if not EMAIL_ADDR:
        raise RuntimeError("Missing EMAIL_ADDR in .env")
    if not EMAIL_AUTH_CODE:
        raise RuntimeError("Missing EMAIL_AUTH_CODE in .env")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUT_DIR / f"{since_to_output_date(SINCE)}-cmbbilling-raw.csv"

    existing_df = load_existing_output(output_file)
    existing_counter = row_counter(existing_df)

    log(f"output={output_file}")
    log(f"since={SINCE}; mailboxes={MAILBOXES}")

    new_frames: list[pd.DataFrame] = []
    scanned_count = 0
    matched_count = 0
    extracted_mail_count = 0
    skipped_existing_count = 0
    extracted_row_count = 0

    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as mail:
        login_status, login_data = mail.login(EMAIL_ADDR, EMAIL_AUTH_CODE)
        if login_status != "OK":
            raise RuntimeError(f"IMAP login failed: {login_status}, {login_data}")

        send_imap_id(mail)

        for mailbox in get_target_mailboxes(mail):
            status, data, selected_name = select_mailbox(mail, mailbox)
            display_name = decode_mailbox_name(selected_name.strip('"'))

            if status != "OK":
                log(f"skip mailbox={mailbox}; select failed={data}")
                continue

            status, data = mail.search(None, "SINCE", SINCE)
            if status != "OK":
                log(f"skip mailbox={display_name}; search failed={data}")
                continue

            mail_ids = data[0].split() if data and data[0] else []
            log(f"mailbox={display_name}; mails_found={len(mail_ids)}")

            for mail_id in mail_ids:
                scanned_count += 1

                header = fetch_header(mail, mail_id)
                if header is None:
                    continue

                subject = decode_header_value(header.get("Subject"))
                sender = decode_header_value(header.get("From"))

                if not match_keywords(subject, sender):
                    continue

                matched_count += 1

                msg = fetch_full_message(mail, mail_id)
                if msg is None:
                    continue

                html, _ = extract_bodies(msg)
                if not html:
                    continue

                df = extract_cmb_transaction_rows_from_html(html)
                if df.empty:
                    continue

                mail_counter = row_counter(df)
                if already_extracted(mail_counter, existing_counter):
                    skipped_existing_count += 1
                    continue

                new_frames.append(df)
                existing_counter.update(mail_counter)
                extracted_mail_count += 1
                extracted_row_count += len(df)

        mail.logout()

    if new_frames:
        final_df = pd.concat([existing_df, *new_frames], ignore_index=True)
    else:
        final_df = existing_df

    final_df = final_df[RAW_COLUMNS]
    final_df.to_csv(output_file, index=False, encoding="utf-8-sig")

    log(
        "done: "
        f"scanned={scanned_count}, "
        f"matched={matched_count}, "
        f"new_mails={extracted_mail_count}, "
        f"skipped_existing={skipped_existing_count}, "
        f"new_rows={extracted_row_count}, "
        f"total_rows={len(final_df)}"
    )


if __name__ == "__main__":
    main()
