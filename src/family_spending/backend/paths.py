from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from family_spending.enrichment_store import ENRICHMENT_STATE_FILE
from family_spending.manual_source import MANUAL_SOURCE_RECORDS_FILE
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
    """Collect the persistent files that participate in the financial backend runtime."""

    transactions: Path = TRANSACTIONS_FILE
    manual_source: Path = MANUAL_SOURCE_RECORDS_FILE
    source_links: Path = TRANSACTION_SOURCE_LINKS_FILE
    enrichment_state: Path = ENRICHMENT_STATE_FILE
    merchants: Path = MERCHANTS_FILE
    categories: Path = CATEGORIES_FILE
    spending_statistics: Path = SPENDING_STATISTICS_FILE
    financial_summary: Path = FINANCIAL_SUMMARY_FILE
    emails: Path = EMAILS_DIR

    @classmethod
    def for_generation(
        cls,
        *,
        transactions: Path,
        merchants: Path,
        categories: Path,
        spending_statistics: Path,
        emails: Path,
        manual_source: Path | None = None,
        source_links: Path | None = None,
        enrichment_state: Path | None = None,
        financial_summary: Path | None = None,
    ) -> BackendPaths:
        """Derive optional runtime sidecars beside a caller-selected transaction file."""
        data_root = transactions.parent
        return cls(
            transactions=transactions,
            manual_source=manual_source or data_root / MANUAL_SOURCE_RECORDS_FILE.name,
            source_links=source_links or data_root / TRANSACTION_SOURCE_LINKS_FILE.name,
            enrichment_state=enrichment_state or data_root / ENRICHMENT_STATE_FILE.name,
            merchants=merchants,
            categories=categories,
            spending_statistics=spending_statistics,
            financial_summary=(
                financial_summary
                or spending_statistics.with_name(FINANCIAL_SUMMARY_FILE.name)
            ),
            emails=emails,
        )
