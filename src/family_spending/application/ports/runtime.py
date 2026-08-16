from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar

from family_spending.application.ports.storage import UnitOfWork
from family_spending.domain.enrichment import EnrichmentDecision, ResolvedEnrichment
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.refund import NetConsumption
from family_spending.domain.source import SourceRecord
from family_spending.domain.transaction import SourceLink, Transaction

ResultT = TypeVar("ResultT")


class HouseholdReadModel(Protocol):
    source_records: tuple[SourceRecord, ...]
    unreconciled_source_record_ids: tuple[str, ...]
    source_links: tuple[SourceLink, ...]
    transactions: tuple[Transaction, ...]
    mappings: MappingCatalog
    enrichment_decisions: tuple[EnrichmentDecision, ...]
    enrichments: tuple[ResolvedEnrichment, ...]
    net_consumption: tuple[NetConsumption, ...]
    spending_payload: Mapping[str, object]
    financial_payload: Mapping[str, object]


class QueryIndexesReadModel(Protocol):
    transaction_by_id: Mapping[str, Transaction]
    authoritative_source_by_transaction_id: Mapping[str, SourceRecord]
    enrichment_by_transaction_id: Mapping[str, ResolvedEnrichment]
    transaction_ids_by_month: Mapping[str, tuple[str, ...]]
    transaction_ids_by_description: Mapping[str, tuple[str, ...]]
    transaction_ids_by_merchant: Mapping[str, tuple[str, ...]]
    transaction_ids_by_review_signal: Mapping[str, tuple[str, ...]]
    unclassified_transaction_ids: tuple[str, ...]


class RuntimeStateReadModel(Protocol):
    generation: int
    household: HouseholdReadModel
    indexes: QueryIndexesReadModel


class RuntimeReader(Protocol):
    """Expose the latest immutable generation without coupling Application to Runtime implementation."""

    def current_state(self) -> RuntimeStateReadModel: ...


class MutationExecutor(Protocol):
    """Serialize one authoritative Application mutation and publish only after commit."""

    def execute(
        self,
        *,
        label: str,
        unit_of_work: UnitOfWork,
        mutation: Callable[[], ResultT],
    ) -> ResultT: ...
