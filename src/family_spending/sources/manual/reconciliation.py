from __future__ import annotations

from family_spending.domain.reconciliation import (
    ReconciliationError,
    ReconciliationProposal,
    ReconciliationState,
    build_candidate,
    select_unambiguous_candidate,
    transaction_merchant_hint,
)
from family_spending.domain.source import SourceRecord
from family_spending.domain.transaction import SourceLink, SourceLinkRole
from family_spending.sources.manual.model import MANUAL_SOURCE_TYPE


class ManualReconciliationPolicy:
    """Manual evidence deduplicates conservatively and never takes authority from an existing fact."""

    source_type = MANUAL_SOURCE_TYPE
    processing_order = 200

    def role_for_existing_link(
        self,
        record: SourceRecord,
        link: SourceLink,
        state: ReconciliationState,
    ) -> SourceLinkRole:
        del record, state
        return link.role

    def resolve_unlinked(
        self,
        record: SourceRecord,
        state: ReconciliationState,
    ) -> ReconciliationProposal:
        if record.source_type != self.source_type:
            raise ReconciliationError(
                f"Manual policy cannot process source type {record.source_type!r}"
            )

        record_merchant = state.hints.merchant_by_source_record_id.get(record.id)
        candidates = tuple(
            candidate
            for transaction in state.transactions
            if (
                candidate := build_candidate(
                    record,
                    transaction,
                    record_merchant=record_merchant,
                    transaction_merchant=transaction_merchant_hint(state, transaction.id),
                )
            )
            is not None
        )
        selected, ambiguous = select_unambiguous_candidate(candidates)
        if ambiguous:
            candidate_ids = sorted(candidate.transaction.id for candidate in candidates)
            raise ReconciliationError(
                f"Manual SourceRecord {record.id!r} matches multiple Transactions: "
                f"{candidate_ids!r}"
            )
        if selected is None:
            return ReconciliationProposal(
                transaction_id=None,
                role="authoritative",
            )
        return ReconciliationProposal(
            transaction_id=selected.transaction.id,
            role="supporting",
            evidence=selected.evidence,
        )
