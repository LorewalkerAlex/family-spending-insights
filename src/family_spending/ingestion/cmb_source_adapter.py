from __future__ import annotations

from dataclasses import dataclass

from family_spending.ingestion.cmb_email_transactions import CmbTransaction
from family_spending.source_records import SourceAdapter, SourceRecord

CMB_SOURCE_TYPE = "cmb_email"
CMB_CURRENCY = "CNY"


@dataclass(frozen=True)
class CmbSourceProvenance:
    source_email: str
    source_index: int


class CmbSourceAdapter(SourceAdapter[CmbTransaction, CmbSourceProvenance]):
    def adapt(self, item: CmbTransaction) -> SourceRecord[CmbSourceProvenance]:
        """Preserve every existing CMB field while reclassifying the old transaction ID as source identity."""
        return SourceRecord(
            id=item.transaction_id,
            source_type=CMB_SOURCE_TYPE,
            transaction_type="expense",
            transaction_date=item.transaction_date,
            amount=item.amount,
            currency=CMB_CURRENCY,
            description=item.description,
            provenance=CmbSourceProvenance(
                source_email=item.source_email,
                source_index=item.source_index,
            ),
        )
