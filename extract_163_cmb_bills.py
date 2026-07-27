import os
import re
import imaplib
import email
from email import policy
from email.message import EmailMessage
from pathlib import Path
from io import StringIO
from datetime import datetime
from typing import Iterable

import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from imapclient import imap_utf7


# ============================================================
# Load .env
# ============================================================

load_dotenv()


# ============================================================
# Config
# ============================================================

IMAP_HOST = os.getenv("IMAP_HOST", "imap.163.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))

EMAIL_ADDR = os.getenv("EMAIL_ADDR")
EMAIL_AUTH_CODE = os.getenv("EMAIL_AUTH_CODE")

SINCE = os.getenv("SINCE", "01-Jan-2023")
MAILBOXES = os.getenv("MAILBOXES", "INBOX")

OUT_DIR = Path(os.getenv("OUT_DIR", "cmb_bill_output"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

PRINT_EVERY = int(os.getenv("PRINT_EVERY", "100"))
PRINT_ALL_HEADERS = os.getenv("PRINT_ALL_HEADERS", "false").lower() == "true"

# 0 表示不限制数量
MAX_MAILS_PER_MAILBOX = int(os.getenv("MAX_MAILS_PER_MAILBOX", "0"))

# 每封邮件最多保留多少个候选 HTML table
MAX_CANDIDATE_TABLES_PER_MAIL = int(os.getenv("MAX_CANDIDATE_TABLES_PER_MAIL", "5"))

DEFAULT_KEYWORDS = [
    "招商",
    "招商银行",
    "招商银行信用卡",
    "招商银行信用卡电子账单",
    "信用卡",
    "信用卡账单",
    "电子账单",
    "账单",
    "还款",
    "应还款",
    "本期账单",
    "CMB",
    "cmb",
    "cmbchina",
    "95555",
    "4008205555",
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


# ============================================================
# Logging helpers
# ============================================================

def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(stage: str, message: str = ""):
    if message:
        print(f"[{now_str()}] [{stage}] {message}", flush=True)
    else:
        print(f"[{now_str()}] [{stage}]", flush=True)


def log_block(title: str):
    print("\n" + "=" * 80, flush=True)
    print(title, flush=True)
    print("=" * 80, flush=True)


def log_kv(data: dict):
    for key, value in data.items():
        print(f"  - {key}: {value}", flush=True)


def shorten(text: str, max_len: int = 120) -> str:
    text = text or ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ============================================================
# Basic helpers
# ============================================================

def safe_filename(text: str, max_len: int = 120) -> str:
    """
    Windows 文件名安全处理。
    """
    text = text or "mail"
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", "_", text).strip("_")
    return text[:max_len] or "mail"


def decode_header_value(value) -> str:
    """
    将邮件头中的 Subject / From / Date 等字段转成普通字符串。
    policy.default 通常已经会自动解码大部分 MIME header。
    """
    if value is None:
        return ""
    return str(value)


def extract_first_tuple_payload(data):
    """
    imaplib.fetch 返回的数据结构有时会混入 b')' 之类的元素。
    这里统一取第一个 tuple payload。
    """
    if not data:
        return None

    for item in data:
        if isinstance(item, tuple) and len(item) >= 2:
            return item[1]

    return None


def match_keywords(subject: str, sender: str) -> list[str]:
    """
    根据邮件标题和发件人匹配关键词。
    """
    text = f"{subject} {sender}".lower()
    return [keyword for keyword in KEYWORDS if keyword.lower() in text]


def html_to_text(html: str) -> str:
    """
    将 HTML 正文转换成可读文本。
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return soup.get_text("\n", strip=True)


def extract_bodies(msg: EmailMessage) -> tuple[str, str]:
    """
    从邮件中提取 HTML 正文和纯文本正文。
    返回: (html_text, plain_text)
    """
    html_parts = []
    text_parts = []

    parts: Iterable[EmailMessage]
    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]

    for part in parts:
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()

        if disposition == "attachment":
            continue

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


# ============================================================
# IMAP helpers
# ============================================================

def send_imap_id(mail: imaplib.IMAP4_SSL):
    """
    网易 163/126 邮箱需要在 login 之后、select 之前发送 IMAP ID。
    否则可能出现：
    SELECT Unsafe Login. Please contact kefu@188.com for help
    """
    log_block("STAGE 2.5 - 发送 IMAP ID 客户端信息")

    try:
        # 允许 imaplib 在 AUTH 状态下发送 ID 命令
        imaplib.Commands["ID"] = ("AUTH",)

        client_id = (
            "name", "mail-billing-extract",
            "version", "1.0.0",
            "vendor", "local-python-script",
            "contact", EMAIL_ADDR or "unknown",
        )

        id_payload = '("' + '" "'.join(client_id) + '")'

        log("IMAP_ID", f"发送 ID payload = {id_payload}")

        typ, data = mail._simple_command("ID", id_payload)
        log("IMAP_ID", f"ID command result: typ={typ}, data={data}")

        try:
            response = mail._untagged_response(typ, data, "ID")
            log("IMAP_ID", f"ID response: {response}")
        except Exception as exc:
            log("IMAP_ID", f"读取 ID response 时出现非致命异常：{exc}")

        return typ, data

    except Exception as exc:
        log("IMAP_ID", f"发送 IMAP ID 失败：{exc}")
        return "EXCEPTION", str(exc)


def encode_mailbox_name(mailbox: str) -> str:
    """
    将中文邮箱文件夹名转换成 IMAP Modified UTF-7。
    例如：
    招行信用卡 -> &YtuITE,hdShTYQ-
    INBOX 保持不变。
    """
    try:
        mailbox.encode("ascii")
        return mailbox
    except UnicodeEncodeError:
        encoded = imap_utf7.encode(mailbox).decode("ascii")
        log("MAILBOX_NAME", f"中文文件夹名转换：{mailbox} -> {encoded}")
        return encoded


def decode_mailbox_name(raw_name: str) -> str:
    """
    将 IMAP Modified UTF-7 文件夹名转换成人类可读中文名。
    """
    try:
        return imap_utf7.decode(raw_name.encode("ascii"))
    except Exception:
        return raw_name


def parse_mailbox_names(mail: imaplib.IMAP4_SSL) -> list[str]:
    """
    获取邮箱文件夹列表。
    MAILBOXES=ALL 时使用。

    返回 raw_name，用于后续 select。
    同时打印 display_name，方便人工确认。
    """
    log_block("STAGE 3 - 列出邮箱文件夹")

    status, data = mail.list()

    log("MAILBOX_LIST", f"mail.list() status = {status}")

    if status != "OK" or not data:
        log("MAILBOX_LIST", "没有拿到文件夹列表，回退到 INBOX")
        return ["INBOX"]

    mailboxes = []

    for raw in data:
        if not raw:
            continue

        line = raw.decode("ascii", errors="ignore")

        match = re.search(r'"([^"]+)"\s*$', line)
        if match:
            raw_name = match.group(1)
        else:
            parts = line.split()
            raw_name = parts[-1].strip('"') if parts else ""

        if not raw_name:
            continue

        display_name = decode_mailbox_name(raw_name)

        mailboxes.append(raw_name)

        print(f"  RAW: {line}", flush=True)
        print(f"    raw_name     = {raw_name}", flush=True)
        print(f"    display_name = {display_name}", flush=True)

    if "INBOX" not in mailboxes:
        mailboxes.insert(0, "INBOX")

    mailboxes = list(dict.fromkeys(mailboxes))

    log("MAILBOX_LIST", f"解析到 {len(mailboxes)} 个文件夹")

    return mailboxes


def get_target_mailboxes(mail: imaplib.IMAP4_SSL) -> list[str]:
    """
    根据 .env 中的 MAILBOXES 决定扫描哪些文件夹。

    支持：
    MAILBOXES=INBOX
    MAILBOXES=ALL
    MAILBOXES=招行信用卡
    MAILBOXES=INBOX,招行信用卡
    """
    value = MAILBOXES.strip()

    if value.upper() == "ALL":
        return parse_mailbox_names(mail)

    requested = [x.strip() for x in value.split(",") if x.strip()]
    if not requested:
        return ["INBOX"]

    result = []
    for mailbox in requested:
        result.append(mailbox)

    return result or ["INBOX"]


def select_mailbox(mail: imaplib.IMAP4_SSL, mailbox: str):
    """
    选择邮箱文件夹。

    imaplib 不能直接 select 中文文件夹名。
    中文名必须先转成 IMAP Modified UTF-7。
    """
    encoded_mailbox = encode_mailbox_name(mailbox)

    attempts = [encoded_mailbox]

    if not (encoded_mailbox.startswith('"') and encoded_mailbox.endswith('"')):
        quoted = f'"{encoded_mailbox}"'
        if quoted not in attempts:
            attempts.append(quoted)

    last_status = None
    last_data = None
    last_attempt = None

    for attempt in attempts:
        log("MAILBOX_SELECT", f"尝试 select: {attempt}")

        try:
            status, data = mail.select(attempt)
        except Exception as exc:
            log("MAILBOX_SELECT", f"select 异常: {attempt} | {exc}")
            last_status = "EXCEPTION"
            last_data = str(exc)
            last_attempt = attempt
            continue

        log("MAILBOX_SELECT", f"select 返回: status={status}, data={data}")

        if status == "OK":
            return status, data, attempt

        last_status = status
        last_data = data
        last_attempt = attempt

    return last_status, last_data, last_attempt


def fetch_header(mail: imaplib.IMAP4_SSL, mail_id: bytes):
    """
    只读取邮件头，避免一开始就下载完整邮件。
    """
    status, data = mail.fetch(
        mail_id,
        "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE TO MESSAGE-ID)])",
    )

    if status != "OK":
        return None, status, data

    raw_header = extract_first_tuple_payload(data)
    if not raw_header:
        return None, status, data

    header_msg = email.message_from_bytes(raw_header, policy=policy.default)
    return header_msg, status, data


def fetch_full_message(mail: imaplib.IMAP4_SSL, mail_id: bytes):
    """
    下载完整邮件。
    """
    status, data = mail.fetch(mail_id, "(RFC822)")

    if status != "OK":
        return None, None, status, data

    raw_msg = extract_first_tuple_payload(data)
    if not raw_msg:
        return None, None, status, data

    msg = email.message_from_bytes(raw_msg, policy=policy.default)
    return raw_msg, msg, status, data


# ============================================================
# Table extraction helpers
# ============================================================

def normalize_cell(value) -> str:
    """
    将 HTML 表格中的任意单元格值安全转成字符串。
    pd.read_html 解析出来的值可能是 str / int / float / NaN。
    """
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    return re.sub(r"\s+", " ", str(value)).strip()


def clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    清理 pandas 从 HTML 中解析出的表格。
    """
    df = df.copy()

    # 删除全空行、全空列
    df = df.dropna(how="all")
    df = df.dropna(axis=1, how="all")

    if df.empty:
        return df

    # 安全地把所有单元格转成字符串并清理空白
    df = df.map(normalize_cell)

    # 删除全空行
    df = df[
        ~df.apply(
            lambda row: all(str(x).strip() == "" for x in row),
            axis=1,
        )
    ]

    return df


def table_fingerprint(df: pd.DataFrame) -> str:
    """
    用于去重的简单指纹。
    """
    preview = df.head(80).to_csv(index=False)
    return str(hash(preview))


def score_table(df: pd.DataFrame) -> int:
    """
    给候选表简单打分。
    行列越多越可能是数据表；
    包含交易相关关键词则加分。
    """
    rows, cols = df.shape
    score = rows * cols

    joined = " ".join(df.head(20).astype(str).fillna("").values.flatten())
    joined_lower = joined.lower()

    important_terms = [
        "交易",
        "记账",
        "摘要",
        "商户",
        "人民币",
        "美元",
        "金额",
        "卡号",
        "消费",
        "date",
        "amount",
        "transaction",
        "merchant",
    ]

    for term in important_terms:
        if term.lower() in joined_lower:
            score += 500

    return score


def parse_amount(value: str):
    """
    将 '¥ 162.00' / '&yen; 162.00' / '162.00' 转成 float。
    失败则返回 None。
    """
    if value is None:
        return None

    text = str(value)
    text = text.replace("\xa0", " ")
    text = text.replace("&nbsp;", " ")
    text = text.replace("&yen;", "¥")
    text = text.replace("￥", "¥")
    text = re.sub(r"\s+", " ", text).strip()

    # 去掉币种符号、逗号等
    text = text.replace("¥", "")
    text = text.replace(",", "")
    text = text.strip()

    try:
        return float(text)
    except ValueError:
        return None


def normalize_text(value: str) -> str:
    """
    清理 HTML 文本中的空白。
    """
    if value is None:
        return ""

    value = str(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def extract_cmb_transaction_rows_from_html(html: str) -> pd.DataFrame:
    """
    专门解析招商银行信用卡电子账单 HTML 中的交易明细行。

    依据当前账单模板：
    - 每条交易通常位于 TABLE width=643 height=18
    - 一行 8 个 TD
    - 第 2 个 TD 是交易日 MMDD
    - 第 3 个 TD 是记账日 MMDD
    """
    soup = BeautifulSoup(html, "html.parser")

    rows = []

    for table in soup.find_all("table"):
        width = str(table.get("width", "")).strip()
        height = str(table.get("height", "")).strip()

        # 当前招行账单交易明细行的内层 table
        if width != "643" or height != "18":
            continue

        tr = table.find("tr")
        if tr is None:
            continue

        cells = [
            normalize_text(td.get_text(" ", strip=True))
            for td in tr.find_all("td", recursive=False)
        ]

        # 典型结构：
        # ['', '0606', '0607', '财付通-美团平台商户', '¥ 162.00', '9042', 'CN', '162.00']
        if len(cells) != 8:
            continue

        _, transaction_mmdd, post_mmdd, description, raw_amount, card_last4, country, cny_amount = cells

        if not re.fullmatch(r"\d{4}", transaction_mmdd):
            continue

        if not re.fullmatch(r"\d{4}", post_mmdd):
            continue

        if not description:
            continue

        # 金额列至少有一个能解析出数字
        raw_amount_value = parse_amount(raw_amount)
        cny_amount_value = parse_amount(cny_amount)

        if raw_amount_value is None and cny_amount_value is None:
            continue

        rows.append(
            {
                "transaction_mmdd": transaction_mmdd,
                "post_mmdd": post_mmdd,
                "description": description,
                "raw_amount_text": raw_amount,
                "raw_amount": raw_amount_value,
                "card_last4": card_last4,
                "country_or_region": country,
                "cny_amount_text": cny_amount,
                "cny_amount": cny_amount_value,
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# Output helpers
# ============================================================

def write_outputs(
    scanned_headers: list[dict],
    matched_rows: list[dict],
    all_tables: list[pd.DataFrame],
):
    """
    写出最终结果文件。
    """
    log_block("STAGE 8 - 写出汇总结果")

    scanned_csv = OUT_DIR / "scanned_mail_headers.csv"
    matched_csv = OUT_DIR / "matched_cmb_bill_mails.csv"

    pd.DataFrame(scanned_headers).to_csv(
        scanned_csv,
        index=False,
        encoding="utf-8-sig",
    )

    pd.DataFrame(matched_rows).to_csv(
        matched_csv,
        index=False,
        encoding="utf-8-sig",
    )

    log("OUTPUT", f"已写出扫描过的邮件 header 列表：{scanned_csv}")
    log("OUTPUT", f"已写出命中账单候选邮件列表：{matched_csv}")

    if all_tables:
        xlsx_path = OUT_DIR / "cmb_bill_candidate_tables.xlsx"

        with pd.ExcelWriter(xlsx_path) as writer:
            for i, df in enumerate(all_tables, start=1):
                sheet_name = f"table_{i}"[:31]
                df.to_excel(writer, sheet_name=sheet_name, index=False)

        log("OUTPUT", f"已写出候选 HTML 表格汇总：{xlsx_path}")
    else:
        log("OUTPUT", "没有解析到候选 HTML table。")

    log_block("FINAL SUMMARY")
    log_kv(
        {
            "输出目录": OUT_DIR.resolve(),
            "扫描过的邮件 header 数量": len(scanned_headers),
            "命中的疑似账单邮件数量": len(matched_rows),
            "保留的候选 HTML table 数量": len(all_tables),
            "scanned_mail_headers.csv": scanned_csv,
            "matched_cmb_bill_mails.csv": matched_csv,
        }
    )

    if matched_rows:
        print("\n命中的疑似账单邮件：", flush=True)
        for i, row in enumerate(matched_rows, start=1):
            print(
                f"  {i}. [{row.get('mailbox_display')}] "
                f"{row.get('date')} | "
                f"{row.get('from')} | "
                f"{row.get('subject')} | "
                f"keywords={row.get('matched_keywords')} | "
                f"candidate_tables={row.get('parsed_candidate_table_count')}",
                flush=True,
            )


# ============================================================
# Main mailbox processing
# ============================================================

def process_mailbox(
    mail: imaplib.IMAP4_SSL,
    mailbox: str,
    scanned_headers: list[dict],
    matched_rows: list[dict],
    all_tables: list[pd.DataFrame],
):
    mailbox_display = decode_mailbox_name(encode_mailbox_name(mailbox))

    log_block(f"STAGE 4 - 扫描邮箱文件夹：{mailbox_display}")

    status, select_data, selected_name = select_mailbox(mail, mailbox)

    if status != "OK":
        log(
            "MAILBOX_SELECT",
            f"跳过文件夹：{mailbox}，最后尝试名称：{selected_name}，状态：{status}，详情：{select_data}",
        )
        return

    selected_display = decode_mailbox_name(selected_name.strip('"'))

    total_in_mailbox = None
    if select_data and len(select_data) > 0:
        try:
            total_in_mailbox = int(select_data[0])
        except Exception:
            total_in_mailbox = select_data[0]

    log_kv(
        {
            "原始 mailbox": mailbox,
            "实际 select 名称": selected_name,
            "显示名称": selected_display,
            "文件夹内邮件总数": total_in_mailbox,
            "搜索起始日期 SINCE": SINCE,
        }
    )

    log_block(f"STAGE 5 - 搜索邮件：{selected_display}")

    status, data = mail.search(None, "SINCE", SINCE)

    log("MAIL_SEARCH", f"search status = {status}")

    if status != "OK":
        log("MAIL_SEARCH", f"搜索失败：{selected_display}，详情：{data}")
        return

    mail_ids = data[0].split() if data and data[0] else []

    if MAX_MAILS_PER_MAILBOX > 0:
        original_count = len(mail_ids)
        mail_ids = mail_ids[:MAX_MAILS_PER_MAILBOX]
        log(
            "MAIL_SEARCH",
            f"命中 {original_count} 封，因 MAX_MAILS_PER_MAILBOX={MAX_MAILS_PER_MAILBOX}，本次只扫描前 {len(mail_ids)} 封",
        )
    else:
        log("MAIL_SEARCH", f"找到 {len(mail_ids)} 封 {SINCE} 之后的邮件")

    if not mail_ids:
        return

    log_block(f"STAGE 6 - 扫描邮件 header 并筛选候选账单：{selected_display}")

    mailbox_match_count = 0

    for idx, mail_id in enumerate(mail_ids, start=1):
        mail_id_str = mail_id.decode("ascii", errors="ignore")

        if idx == 1 or idx % PRINT_EVERY == 0 or idx == len(mail_ids):
            log("HEADER_SCAN", f"进度 {idx}/{len(mail_ids)}，当前 mail_id={mail_id_str}")

        header_msg, header_status, header_data = fetch_header(mail, mail_id)

        if header_msg is None:
            log(
                "HEADER_SCAN",
                f"读取 header 失败：mail_id={mail_id_str}, status={header_status}, data={header_data}",
            )
            continue

        subject = decode_header_value(header_msg.get("Subject"))
        sender = decode_header_value(header_msg.get("From"))
        date = decode_header_value(header_msg.get("Date"))
        to = decode_header_value(header_msg.get("To"))
        message_id = decode_header_value(header_msg.get("Message-ID"))

        matched_keywords = match_keywords(subject, sender)
        is_candidate = bool(matched_keywords)

        scanned_row = {
            "mailbox": selected_name,
            "mailbox_display": selected_display,
            "mail_id": mail_id_str,
            "date": date,
            "from": sender,
            "to": to,
            "subject": subject,
            "message_id": message_id,
            "is_candidate": is_candidate,
            "matched_keywords": ",".join(matched_keywords),
        }
        scanned_headers.append(scanned_row)

        if PRINT_ALL_HEADERS:
            print(
                f"  HEADER {idx}/{len(mail_ids)} | "
                f"candidate={is_candidate} | "
                f"date={shorten(date, 60)} | "
                f"from={shorten(sender, 80)} | "
                f"subject={shorten(subject, 120)}",
                flush=True,
            )

        if not is_candidate:
            continue

        mailbox_match_count += 1

        log_block(f"STAGE 7 - 命中疑似账单邮件 #{mailbox_match_count}")

        log_kv(
            {
                "mailbox": selected_name,
                "mailbox_display": selected_display,
                "mail_id": mail_id_str,
                "date": date,
                "from": sender,
                "to": to,
                "subject": subject,
                "matched_keywords": ", ".join(matched_keywords),
            }
        )

        raw_msg, msg, full_status, full_data = fetch_full_message(mail, mail_id)

        if raw_msg is None or msg is None:
            log(
                "FULL_FETCH",
                f"下载完整邮件失败：mail_id={mail_id_str}, status={full_status}, data={full_data}",
            )
            continue

        html, plain = extract_bodies(msg)

        has_html = bool(html)
        has_plain = bool(plain)

        log_kv(
            {
                "has_html": has_html,
                "html_length": len(html),
                "has_plain": has_plain,
                "plain_length": len(plain),
                "is_multipart": msg.is_multipart(),
            }
        )

        base_name = safe_filename(f"{selected_name}_{date}_{subject}")

        eml_path = OUT_DIR / f"{base_name}.eml"
        html_path = OUT_DIR / f"{base_name}.html"
        txt_path = OUT_DIR / f"{base_name}.txt"

        eml_path.write_bytes(raw_msg)

        if html:
            html_path.write_text(html, encoding="utf-8", errors="ignore")
            text = html_to_text(html)
            txt_path.write_text(text, encoding="utf-8", errors="ignore")
        else:
            html_path = None
            txt_path.write_text(plain, encoding="utf-8", errors="ignore")

        transaction_count = 0

        if html:
            transaction_df = extract_cmb_transaction_rows_from_html(html)

            if not transaction_df.empty:
                transaction_df.insert(0, "mail_subject", subject)
                transaction_df.insert(1, "mail_date", date)
                transaction_df.insert(2, "mailbox_display", selected_display)

                transaction_csv_path = OUT_DIR / f"{base_name}_transactions.csv"
                transaction_df.to_csv(transaction_csv_path, index=False, encoding="utf-8-sig")

                all_tables.append(transaction_df)

                transaction_count = len(transaction_df)
            else:
                transaction_csv_path = ""

        log_kv(
            {
                "saved_eml": eml_path,
                "saved_html": html_path if html_path else "",
                "saved_txt": txt_path,
                "parsed_transaction_count": transaction_count,
                "saved_transactions_csv": transaction_csv_path,
            }
        )

        matched_rows.append(
            {
                "mailbox": selected_name,
                "mailbox_display": selected_display,
                "mail_id": mail_id_str,
                "date": date,
                "from": sender,
                "to": to,
                "subject": subject,
                "message_id": message_id,
                "matched_keywords": ",".join(matched_keywords),
                "has_html": has_html,
                "html_length": len(html),
                "has_plain": has_plain,
                "plain_length": len(plain),
                "parsed_transaction_count": transaction_count,
                "transactions_csv_file": str(transaction_csv_path) if transaction_count else "",
                "eml_file": str(eml_path),
                "html_file": str(html_path) if html_path else "",
                "txt_file": str(txt_path),
            }
        )

    log_block(f"STAGE 7 SUMMARY - 文件夹扫描完成：{selected_display}")
    log_kv(
        {
            "文件夹": selected_display,
            "本文件夹搜索到的邮件数": len(mail_ids),
            "本文件夹命中的候选账单邮件数": mailbox_match_count,
        }
    )


# ============================================================
# Main
# ============================================================

def main():
    log_block("STAGE 1 - 加载配置")

    if not EMAIL_ADDR:
        raise RuntimeError("缺少 EMAIL_ADDR，请在 .env 中配置。")

    if not EMAIL_AUTH_CODE:
        raise RuntimeError("缺少 EMAIL_AUTH_CODE，请在 .env 中配置 163 邮箱授权码。")

    log_kv(
        {
            "IMAP_HOST": IMAP_HOST,
            "IMAP_PORT": IMAP_PORT,
            "EMAIL_ADDR": EMAIL_ADDR,
            "SINCE": SINCE,
            "MAILBOXES": MAILBOXES,
            "OUT_DIR": OUT_DIR.resolve(),
            "KEYWORDS": KEYWORDS,
            "PRINT_EVERY": PRINT_EVERY,
            "PRINT_ALL_HEADERS": PRINT_ALL_HEADERS,
            "MAX_MAILS_PER_MAILBOX": MAX_MAILS_PER_MAILBOX,
            "MAX_CANDIDATE_TABLES_PER_MAIL": MAX_CANDIDATE_TABLES_PER_MAIL,
        }
    )

    scanned_headers = []
    matched_rows = []
    all_tables = []

    log_block("STAGE 2 - 连接并登录 IMAP")

    with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as mail:
        log("IMAP_CONNECT", f"已连接 {IMAP_HOST}:{IMAP_PORT}")

        login_status, login_data = mail.login(EMAIL_ADDR, EMAIL_AUTH_CODE)

        log("IMAP_LOGIN", f"login status = {login_status}, data = {login_data}")

        if login_status != "OK":
            raise RuntimeError(f"登录失败：{login_status}, {login_data}")

        send_imap_id(mail)

        target_mailboxes = get_target_mailboxes(mail)

        log_block("STAGE 3 SUMMARY - 准备扫描的文件夹")
        for i, mailbox in enumerate(target_mailboxes, start=1):
            display = decode_mailbox_name(encode_mailbox_name(mailbox))
            print(f"  {i}. {display} ({encode_mailbox_name(mailbox)})", flush=True)

        for mailbox in target_mailboxes:
            process_mailbox(
                mail=mail,
                mailbox=mailbox,
                scanned_headers=scanned_headers,
                matched_rows=matched_rows,
                all_tables=all_tables,
            )

        logout_status, logout_data = mail.logout()
        log("IMAP_LOGOUT", f"logout status = {logout_status}, data = {logout_data}")

    write_outputs(
        scanned_headers=scanned_headers,
        matched_rows=matched_rows,
        all_tables=all_tables,
    )


if __name__ == "__main__":
    main()