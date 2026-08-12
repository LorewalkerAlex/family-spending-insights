from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DATA_DIR = Path("data")
EMAILS_DIR = DATA_DIR / "emails"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
MAPPINGS_DIR = DATA_DIR / "mappings"
MERCHANTS_FILE = MAPPINGS_DIR / "merchants.yaml"
CATEGORIES_FILE = MAPPINGS_DIR / "categories.yaml"
TRANSACTIONS_FILE = DATA_DIR / "transactions.csv"
REPORTS_DIR = DATA_DIR / "reports"
SPENDING_STATISTICS_FILE = REPORTS_DIR / "spending_statistics.json"
FINANCIAL_SUMMARY_FILE = REPORTS_DIR / "financial_summary.json"


@dataclass(frozen=True)
class EmailCredentials:
    address: str
    auth_code: str


@dataclass(frozen=True)
class Imap163Settings:
    host: str
    port: int
    mailbox: str
    subject_keyword: str
    since: str
    output_dir: Path


# Non-private settings live in code so one reviewed configuration defines the
# real local workflow instead of being duplicated across shell environments.
IMAP_163 = Imap163Settings(
    host="imap.163.com",
    port=993,
    mailbox="招行信用卡",
    subject_keyword="招商银行信用卡电子账单",
    since="01-Sep-2025",
    output_dir=EMAILS_DIR,
)


def load_email_credentials(
    environ: Mapping[str, str] | None = None,
) -> EmailCredentials:
    """Load secrets at runtime so importing project modules has no side effects."""
    if environ is None:
        load_dotenv()
        environ = os.environ
    address = environ.get("EMAIL_ADDR", "").strip()
    auth_code = environ.get("EMAIL_AUTH_CODE", "").strip()
    if not address:
        raise ValueError("Missing required environment variable: EMAIL_ADDR")
    if not auth_code:
        raise ValueError(
            "Missing required environment variable: EMAIL_AUTH_CODE"
        )

    return EmailCredentials(
        address=address,
        auth_code=auth_code,
    )
