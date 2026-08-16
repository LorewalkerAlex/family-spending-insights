from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from threading import Lock
from types import MappingProxyType
from typing import Protocol

from family_spending.application.ports.storage import (
    EnrichmentDecisionStore,
    IdentityStore,
    MappingStore,
)
from family_spending.application.source_registry import SourceRegistry
from family_spending.domain.enrichment import (
    EnrichmentDecision,
    ResolvedEnrichment,
    consumption_review_signals,
    resolve_enrichments,
)
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.refund import NetConsumption
from family_spending.domain.source import SourceRecord
from family_spending.domain.transaction import (
    SourceLink,
    Transaction,
    rebuild_transactions_from_source_links,
)
from family_spending.projections.financial import build_financial_projection
from family_spending.projections.spending import build_spending_projection


class RuntimeBuildError(RuntimeError):
    """Raised when durable state cannot be rehydrated into one coherent runtime snapshot."""


class RuntimeNotReadyError(RuntimeError):
    """Raised when callers request runtime state before bootstrap has completed."""


class StatementDateProvider(Protocol):
    """Expose statement metadata keyed by immutable evidence identity."""

    def load_statement_dates_by_evidence(self) -> Mapping[str, date]: ...


def _freeze_json(value: object) -> object:
    """Recursively freeze projection payloads before publishing them to concurrent readers."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True)
class HouseholdSnapshot:
    """Immutable financial snapshot rebuilt entirely from canonical persistent state."""

    source_records: tuple[SourceRecord, ...]
    unreconciled_source_record_ids: tuple[str, ...]
    source_links: tuple[SourceLink, ...]
    transactions: tuple[Transaction, ...]
    mappings: MappingCatalog
    enrichment_decisions: tuple[EnrichmentDecision, ...]
    enrichments: tuple[ResolvedEnrichment, ...]
    statement_dates: frozenset[date]
    net_consumption: tuple[NetConsumption, ...]
    spending_payload: Mapping[str, object]
    financial_payload: Mapping[str, object]


@dataclass(frozen=True)
class QueryIndexes:
    """Immutable query indexes derived from HouseholdSnapshot, never separately persisted."""

    transaction_by_id: Mapping[str, Transaction]
    authoritative_source_by_transaction_id: Mapping[str, SourceRecord]
    enrichment_by_transaction_id: Mapping[str, ResolvedEnrichment]
    transaction_ids_by_month: Mapping[str, tuple[str, ...]]
    transaction_ids_by_description: Mapping[str, tuple[str, ...]]
    transaction_ids_by_merchant: Mapping[str, tuple[str, ...]]
    transaction_ids_by_review_signal: Mapping[str, tuple[str, ...]]
    unclassified_transaction_ids: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeCandidate:
    """Fully built household and indexes ready for one atomic publication."""

    household: HouseholdSnapshot
    indexes: QueryIndexes


@dataclass(frozen=True)
class OperationalState:
    """Non-financial runtime diagnostics published without mutating household state."""

    current_mutation: str | None = None
    queued_mutations: int = 0
    last_successful_mutation_at: datetime | None = None
    last_mutation_error: str | None = None
    last_source_poll_at: datetime | None = None
    last_source_poll_error: str | None = None
    last_scheduler_tick_at: datetime | None = None
    last_scheduler_error: str | None = None


@dataclass(frozen=True)
class RuntimeState:
    """One atomically published runtime generation visible to all readers."""

    generation: int
    household: HouseholdSnapshot
    indexes: QueryIndexes
    operational: OperationalState


def _authoritative_sources(
    records: tuple[SourceRecord, ...],
    links: tuple[SourceLink, ...],
) -> Mapping[str, SourceRecord]:
    records_by_id = {record.id: record for record in records}
    authoritative: dict[str, SourceRecord] = {}
    for link in links:
        if link.role != "authoritative":
            continue
        try:
            authoritative[link.transaction_id] = records_by_id[link.source_record_id]
        except KeyError as exc:
            raise RuntimeBuildError(
                f"SourceLink references missing SourceRecord {link.source_record_id!r}"
            ) from exc
    return MappingProxyType(authoritative)


def _tuple_index(values: dict[str, list[str]]) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType({key: tuple(items) for key, items in values.items()})


def build_query_indexes(household: HouseholdSnapshot) -> QueryIndexes:
    """Build read-optimized indexes while preserving canonical Transaction order within buckets."""
    transaction_by_id = {item.id: item for item in household.transactions}
    if len(transaction_by_id) != len(household.transactions):
        raise RuntimeBuildError("Duplicate Transaction id while building query indexes")

    authoritative = _authoritative_sources(household.source_records, household.source_links)
    enrichment_by_id = {item.transaction_id: item for item in household.enrichments}
    if set(enrichment_by_id) != set(transaction_by_id):
        raise RuntimeBuildError("Resolved Enrichment set does not match Transactions")

    net_by_id = {item.transaction_id: item for item in household.net_consumption}
    by_month: dict[str, list[str]] = {}
    by_description: dict[str, list[str]] = {}
    by_merchant: dict[str, list[str]] = {}
    by_review: dict[str, list[str]] = {}
    unclassified: list[str] = []

    for transaction in household.transactions:
        source = authoritative[transaction.id]
        enrichment = enrichment_by_id[transaction.id]
        by_month.setdefault(transaction.transaction_date.strftime("%Y-%m"), []).append(
            transaction.id
        )
        if source.description is not None:
            by_description.setdefault(source.description, []).append(transaction.id)
        if enrichment.merchant_name is not None:
            by_merchant.setdefault(enrichment.merchant_name, []).append(transaction.id)
        if enrichment.is_unclassified:
            unclassified.append(transaction.id)

        signals = enrichment.review_signals
        net = net_by_id.get(transaction.id)
        if net is not None:
            signals = consumption_review_signals(enrichment, net.spending)
        for signal in signals:
            by_review.setdefault(signal, []).append(transaction.id)

    return QueryIndexes(
        transaction_by_id=MappingProxyType(transaction_by_id),
        authoritative_source_by_transaction_id=authoritative,
        enrichment_by_transaction_id=MappingProxyType(enrichment_by_id),
        transaction_ids_by_month=_tuple_index(by_month),
        transaction_ids_by_description=_tuple_index(by_description),
        transaction_ids_by_merchant=_tuple_index(by_merchant),
        transaction_ids_by_review_signal=_tuple_index(by_review),
        unclassified_transaction_ids=tuple(unclassified),
    )


@dataclass(frozen=True)
class RuntimeSnapshotBuilder:
    """Rehydrate canonical stores into one immutable household candidate without writing state."""

    source_registry: SourceRegistry
    identity_store: IdentityStore
    mapping_store: MappingStore
    enrichment_store: EnrichmentDecisionStore
    statement_date_provider: StatementDateProvider

    def build(self) -> RuntimeCandidate:
        records = self.source_registry.load_records()
        links = self.identity_store.load()
        linked_source_ids = {link.source_record_id for link in links}
        unlinked = tuple(record.id for record in records if record.id not in linked_source_ids)

        transactions = rebuild_transactions_from_source_links(records, links)
        authoritative = _authoritative_sources(records, links)
        mappings = self.mapping_store.load()
        decisions = self.enrichment_store.load()
        enrichments = resolve_enrichments(
            transactions,
            authoritative,
            mappings,
            decisions,
        )
        enrichment_by_id = MappingProxyType(
            {item.transaction_id: item for item in enrichments}
        )
        statement_dates_by_evidence = dict(
            self.statement_date_provider.load_statement_dates_by_evidence()
        )
        record_ids_by_evidence: dict[str, set[str]] = {}
        for record in records:
            record_ids_by_evidence.setdefault(
                record.identity.evidence_identity,
                set(),
            ).add(record.id)
        reconciled_evidence_ids = {
            evidence_identity
            for evidence_identity, record_ids in record_ids_by_evidence.items()
            if record_ids and record_ids <= linked_source_ids
        }
        statement_dates = frozenset(
            statement_date
            for evidence_identity, statement_date in statement_dates_by_evidence.items()
            if evidence_identity in reconciled_evidence_ids
        )
        spending = build_spending_projection(
            transactions,
            authoritative,
            enrichment_by_id,
            statement_dates,
        )
        financial = build_financial_projection(
            transactions,
            spending.statistics,
            statement_dates,
        )
        household = HouseholdSnapshot(
            source_records=records,
            unreconciled_source_record_ids=unlinked,
            source_links=links,
            transactions=transactions,
            mappings=mappings,
            enrichment_decisions=decisions,
            enrichments=enrichments,
            statement_dates=statement_dates,
            net_consumption=spending.refund.net_consumption,
            spending_payload=_freeze_json(spending.payload),  # type: ignore[arg-type]
            financial_payload=_freeze_json(financial.payload),  # type: ignore[arg-type]
        )
        return RuntimeCandidate(household=household, indexes=build_query_indexes(household))


class RuntimeStore:
    """Publish complete runtime generations atomically while queries remain lock-light."""

    def __init__(self) -> None:
        self._state: RuntimeState | None = None
        self._lock = Lock()

    def current_state(self) -> RuntimeState:
        with self._lock:
            state = self._state
        if state is None:
            raise RuntimeNotReadyError("Runtime has not been bootstrapped")
        return state

    def bootstrap(self, candidate: RuntimeCandidate) -> RuntimeState:
        with self._lock:
            if self._state is not None:
                raise RuntimeBuildError("Runtime may only be bootstrapped once")
            self._state = RuntimeState(
                generation=1,
                household=candidate.household,
                indexes=candidate.indexes,
                operational=OperationalState(),
            )
            return self._state

    def publish(self, candidate: RuntimeCandidate, *, mutation_label: str) -> RuntimeState:
        """Swap one fully built household generation after its persistence commit succeeds."""
        with self._lock:
            if self._state is None:
                raise RuntimeNotReadyError("Runtime has not been bootstrapped")
            operational = replace(
                self._state.operational,
                current_mutation=None,
                last_successful_mutation_at=datetime.now(timezone.utc),
                last_mutation_error=None,
            )
            self._state = RuntimeState(
                generation=self._state.generation + 1,
                household=candidate.household,
                indexes=candidate.indexes,
                operational=operational,
            )
            return self._state

    def adjust_queued_mutations(self, delta: int) -> None:
        with self._lock:
            if self._state is None:
                raise RuntimeNotReadyError("Runtime has not been bootstrapped")
            next_count = self._state.operational.queued_mutations + delta
            if next_count < 0:
                raise RuntimeBuildError("Mutation queue depth cannot be negative")
            self._state = replace(
                self._state,
                operational=replace(
                    self._state.operational,
                    queued_mutations=next_count,
                ),
            )

    def begin_mutation(self, label: str) -> None:
        with self._lock:
            if self._state is None:
                raise RuntimeNotReadyError("Runtime has not been bootstrapped")
            self._state = replace(
                self._state,
                operational=replace(
                    self._state.operational,
                    current_mutation=label,
                    last_mutation_error=None,
                ),
            )

    def record_mutation_failure(self, error: BaseException) -> None:
        with self._lock:
            if self._state is None:
                return
            self._state = replace(
                self._state,
                operational=replace(
                    self._state.operational,
                    current_mutation=None,
                    last_mutation_error=str(error),
                ),
            )

    def record_source_poll(self, error: BaseException | None = None) -> None:
        with self._lock:
            if self._state is None:
                return
            self._state = replace(
                self._state,
                operational=replace(
                    self._state.operational,
                    last_source_poll_at=datetime.now(timezone.utc),
                    last_source_poll_error=None if error is None else str(error),
                ),
            )

    def record_scheduler_tick(self, error: BaseException | None = None) -> None:
        with self._lock:
            if self._state is None:
                return
            self._state = replace(
                self._state,
                operational=replace(
                    self._state.operational,
                    last_scheduler_tick_at=datetime.now(timezone.utc),
                    last_scheduler_error=None if error is None else str(error),
                ),
            )
