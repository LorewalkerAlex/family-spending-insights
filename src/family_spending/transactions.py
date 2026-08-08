from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Literal

from family_spending.source_records import SourceRecord, TransactionType

HASH_PREFIX_LENGTH = 24
SourceLinkRole = Literal["authoritative", "supporting"]


class TransactionDataError(RuntimeError):
    """Raised when Transaction relationships cannot be represented without ambiguity."""


@dataclass(frozen=True)
class Transaction:
    id: str
    transaction_type: TransactionType
    transaction_date: date
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class TransactionSourceLink:
    transaction_id: str
    source_record_id: str
    role: SourceLinkRole


def build_transaction_id(source_record: SourceRecord[Any]) -> str:
    """Derive a stable system ID from source identity without reusing the source-owned ID itself."""
    payload = f"transaction\0{source_record.source_type}\0{source_record.id}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:HASH_PREFIX_LENGTH]
    return f"txn_{digest}"


def index_transactions(transactions: tuple[Transaction, ...]) -> Mapping[str, Transaction]:
    """Reject duplicate system identities early so later joins cannot silently select an arbitrary record."""
    indexed: dict[str, Transaction] = {}
    for transaction in transactions:
        if transaction.id in indexed:
            raise TransactionDataError(f"Duplicate transaction id {transaction.id!r}")
        indexed[transaction.id] = transaction
    return MappingProxyType(indexed)


def index_source_records(source_records: tuple[SourceRecord[Any], ...]) -> Mapping[str, SourceRecord[Any]]:
    """Keep Source Record identity unique because reconciliation and legacy overrides both depend on it."""
    indexed: dict[str, SourceRecord[Any]] = {}
    for record in source_records:
        if record.id in indexed:
            raise TransactionDataError(f"Duplicate source record id {record.id!r}")
        indexed[record.id] = record
    return MappingProxyType(indexed)


def index_authoritative_source_records(
    source_records: tuple[SourceRecord[Any], ...],
    links: tuple[TransactionSourceLink, ...],
) -> Mapping[str, SourceRecord[Any]]:
    """Expose one authoritative provenance record per Transaction so source-only fields stay out of core facts."""
    records_by_id = index_source_records(source_records)
    authoritative_by_transaction: dict[str, SourceRecord[Any]] = {}
    for link in links:
        if link.role != "authoritative":
            continue
        if link.transaction_id in authoritative_by_transaction:
            raise TransactionDataError(
                f"Transaction {link.transaction_id!r} has multiple authoritative source records"
            )
        try:
            authoritative_by_transaction[link.transaction_id] = records_by_id[link.source_record_id]
        except KeyError as exc:
            raise TransactionDataError(
                f"Source link references missing source record {link.source_record_id!r}"
            ) from exc
    return MappingProxyType(authoritative_by_transaction)
