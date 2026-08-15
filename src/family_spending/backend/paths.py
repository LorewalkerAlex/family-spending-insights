from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from family_spending.enrichment_store import ENRICHMENT_STATE_FILE
from family_spending.feedback import FEEDBACK_FILE
from family_spending.manual_source import MANUAL_SOURCE_RECORDS_FILE
from family_spending.scheduled_input import SCHEDULED_INPUT_RULES_FILE
from family_spending.settings import (
    CATEGORIES_FILE,
    EMAILS_DIR,
    FINANCIAL_SUMMARY_FILE,
    MERCHANTS_FILE,
    SPENDING_STATISTICS_FILE,
    TRANSACTIONS_FILE,
)
from family_spending.source_link_store import TRANSACTION_SOURCE_LINKS_FILE


@dataclass(frozen=True)
class BackendPaths:
    """Collect every persistent file owned by the local financial backend."""

    transactions: Path = TRANSACTIONS_FILE
    manual_source: Path = MANUAL_SOURCE_RECORDS_FILE
    source_links: Path = TRANSACTION_SOURCE_LINKS_FILE
    enrichment_state: Path = ENRICHMENT_STATE_FILE
    merchants: Path = MERCHANTS_FILE
    categories: Path = CATEGORIES_FILE
    spending_statistics: Path = SPENDING_STATISTICS_FILE
    financial_summary: Path | None = None
    emails: Path = EMAILS_DIR
    scheduled_input_rules: Path | None = None
    feedback: Path | None = None

    def __post_init__(self) -> None:
        """Keep custom/test path sets isolated beside their selected transaction file."""
        data_root = self.transactions.parent
        if self.financial_summary is None:
            object.__setattr__(
                self,
                "financial_summary",
                self.spending_statistics.with_name(FINANCIAL_SUMMARY_FILE.name),
            )
        if self.scheduled_input_rules is None:
            object.__setattr__(
                self,
                "scheduled_input_rules",
                data_root / SCHEDULED_INPUT_RULES_FILE.name,
            )
        if self.feedback is None:
            object.__setattr__(
                self,
                "feedback",
                data_root / FEEDBACK_FILE.name,
            )
