from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Generic, Literal, TypeVar

TransactionType = Literal["income", "expense"]
InputT = TypeVar("InputT")
ProvenanceT = TypeVar("ProvenanceT")


@dataclass(frozen=True)
class SourceRecord(Generic[ProvenanceT]):
    id: str
    source_type: str
    transaction_type: TransactionType
    transaction_date: date
    amount: Decimal
    currency: str
    description: str | None
    provenance: ProvenanceT


class SourceAdapter(ABC, Generic[InputT, ProvenanceT]):
    @abstractmethod
    def adapt(self, item: InputT) -> SourceRecord[ProvenanceT]:
        """Normalize one source-owned item so downstream code never depends on its storage shape."""

    def adapt_all(self, items: tuple[InputT, ...]) -> tuple[SourceRecord[ProvenanceT], ...]:
        """Preserve source order because later reconciliation may use it as a deterministic tie-breaker."""
        return tuple(self.adapt(item) for item in items)
