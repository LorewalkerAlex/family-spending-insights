from __future__ import annotations

from collections.abc import Mapping

from family_spending.application.errors import ApplicationNotFoundError, ApplicationStateError
from family_spending.application.models import ManualInputRecordView, TransactionView
from family_spending.application.ports.runtime import RuntimeReader

MANUAL_SOURCE_TYPE = "manual"


def _thaw(value: object) -> object:
    """Return a transport-safe copy so callers cannot mutate the published Runtime snapshot."""
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class QueryService:
    """Serve read-only Application views from exactly one immutable Runtime generation per query."""

    def __init__(self, *, runtime: RuntimeReader) -> None:
        self._runtime = runtime

    def list_transactions(self) -> tuple[TransactionView, ...]:
        state = self._runtime.current_state()
        return tuple(
            self._view_from_state(state, transaction.id)
            for transaction in state.household.transactions
        )

    def get_transaction(self, transaction_id: str) -> TransactionView:
        state = self._runtime.current_state()
        if transaction_id not in state.indexes.transaction_by_id:
            raise ApplicationNotFoundError(
                f"Transaction {transaction_id!r} does not exist"
            )
        return self._view_from_state(state, transaction_id)

    def list_categories(self) -> tuple[str, ...]:
        state = self._runtime.current_state()
        return tuple(sorted(state.household.mappings.categories))

    def get_spending_statistics(self) -> dict[str, object]:
        state = self._runtime.current_state()
        payload = _thaw(state.household.spending_payload)
        assert isinstance(payload, dict)
        return payload

    def get_financial_summary(self) -> dict[str, object]:
        state = self._runtime.current_state()
        payload = _thaw(state.household.financial_payload)
        assert isinstance(payload, dict)
        return payload

    def list_manual_descriptions(self) -> tuple[str, ...]:
        state = self._runtime.current_state()
        seen: set[str] = set()
        descriptions: list[str] = []
        for record in reversed(state.household.source_records):
            if record.source_type != MANUAL_SOURCE_TYPE:
                continue
            if record.description is None or record.description in seen:
                continue
            seen.add(record.description)
            descriptions.append(record.description)
        return tuple(descriptions)

    def list_manual_inputs(self) -> tuple[ManualInputRecordView, ...]:
        state = self._runtime.current_state()
        links_by_source = {
            link.source_record_id: link for link in state.household.source_links
        }
        views: list[ManualInputRecordView] = []
        for record in reversed(state.household.source_records):
            if record.source_type != MANUAL_SOURCE_TYPE:
                continue
            link = links_by_source.get(record.id)
            if link is None:
                raise ApplicationStateError(
                    f"Manual evidence {record.identity.evidence_identity!r} is pending Source Sync"
                )
            views.append(
                ManualInputRecordView(
                    evidence_id=record.identity.evidence_identity,
                    source_record=record,
                    transaction_id=link.transaction_id,
                    source_role=link.role,
                    transaction=self._view_from_state(state, link.transaction_id),
                )
            )
        return tuple(views)

    @staticmethod
    def _view_from_state(state, transaction_id: str) -> TransactionView:
        """Join one Transaction without rereading Runtime and risking a mixed generation."""
        transaction = state.indexes.transaction_by_id[transaction_id]
        source = state.indexes.authoritative_source_by_transaction_id[transaction_id]
        enrichment = state.indexes.enrichment_by_transaction_id[transaction_id]
        signals = list(enrichment.review_signals)
        for signal, ids in state.indexes.transaction_ids_by_review_signal.items():
            if transaction_id in ids and signal not in signals:
                signals.append(signal)
        return TransactionView(
            transaction=transaction,
            source_record=source,
            enrichment=enrichment,
            review_signals=tuple(signals),
        )
