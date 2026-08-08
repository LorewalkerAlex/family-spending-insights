from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from family_spending.source_records import SourceRecord
from family_spending.transactions import (
    Transaction,
    TransactionSourceLink,
    build_transaction_id,
    index_transactions,
)

ReconciliationAction = Literal["created", "reused"]


class ReconciliationError(RuntimeError):
    """Raised when Source Records cannot be reconciled into an unambiguous Transaction state."""


@dataclass(frozen=True)
class ReconciliationEvidence:
    source_identity_match: bool = False
    amount_match: bool | None = None
    date_distance_days: int | None = None
    merchant_match: bool | None = None


@dataclass(frozen=True)
class ReconciliationDecision:
    source_record_id: str
    transaction_id: str
    action: ReconciliationAction
    evidence: ReconciliationEvidence


@dataclass(frozen=True)
class ReconciliationResult:
    transactions: tuple[Transaction, ...]
    source_links: tuple[TransactionSourceLink, ...]
    decisions: tuple[ReconciliationDecision, ...]


class Reconciler(ABC):
    @abstractmethod
    def reconcile(
        self,
        records: tuple[SourceRecord[Any], ...],
        *,
        existing_transactions: tuple[Transaction, ...] = (),
        existing_links: tuple[TransactionSourceLink, ...] = (),
    ) -> ReconciliationResult:
        """Resolve source identity into system identity while keeping source-specific policy behind this boundary."""


class CmbReconciler(Reconciler):
    SOURCE_TYPE = "cmb_email"

    def reconcile(
        self,
        records: tuple[SourceRecord[Any], ...],
        *,
        existing_transactions: tuple[Transaction, ...] = (),
        existing_links: tuple[TransactionSourceLink, ...] = (),
    ) -> ReconciliationResult:
        """Make CMB records authoritative and idempotent without pretending their IDs are Transaction IDs."""
        transactions_by_id = dict(index_transactions(existing_transactions))
        ordered_transaction_ids = [transaction.id for transaction in existing_transactions]
        links = list(existing_links)
        link_by_source_record: dict[str, TransactionSourceLink] = {}
        for link in existing_links:
            if link.source_record_id in link_by_source_record:
                raise ReconciliationError(
                    f"Source record {link.source_record_id!r} is linked more than once"
                )
            link_by_source_record[link.source_record_id] = link

        seen_records: set[str] = set()
        decisions: list[ReconciliationDecision] = []
        for record in records:
            if record.source_type != self.SOURCE_TYPE:
                raise ReconciliationError(
                    f"CmbReconciler cannot process source type {record.source_type!r}"
                )
            if record.id in seen_records:
                raise ReconciliationError(f"Duplicate CMB source record {record.id!r}")
            seen_records.add(record.id)

            existing_link = link_by_source_record.get(record.id)
            if existing_link is not None:
                transaction = transactions_by_id.get(existing_link.transaction_id)
                if transaction is None:
                    raise ReconciliationError(
                        f"Source record {record.id!r} links to missing transaction {existing_link.transaction_id!r}"
                    )
                if existing_link.role != "authoritative":
                    # CMB is authoritative for credit-card facts, so reusing a prior link must also
                    # upgrade its role; otherwise downstream provenance would contradict reconciliation.
                    upgraded_link = TransactionSourceLink(
                        transaction_id=existing_link.transaction_id,
                        source_record_id=existing_link.source_record_id,
                        role="authoritative",
                    )
                    links = [
                        upgraded_link if link == existing_link else link
                        for link in links
                    ]
                    link_by_source_record[record.id] = upgraded_link
                transactions_by_id[transaction.id] = self._authoritative_transaction(
                    record,
                    transaction.id,
                )
                decisions.append(
                    ReconciliationDecision(
                        source_record_id=record.id,
                        transaction_id=transaction.id,
                        action="reused",
                        evidence=ReconciliationEvidence(source_identity_match=True),
                    )
                )
                continue

            transaction_id = build_transaction_id(record)
            if transaction_id in transactions_by_id:
                raise ReconciliationError(
                    f"Generated transaction id {transaction_id!r} already exists for another source record"
                )
            transaction = self._authoritative_transaction(record, transaction_id)
            transactions_by_id[transaction_id] = transaction
            ordered_transaction_ids.append(transaction_id)
            link = TransactionSourceLink(
                transaction_id=transaction_id,
                source_record_id=record.id,
                role="authoritative",
            )
            links.append(link)
            link_by_source_record[record.id] = link
            decisions.append(
                ReconciliationDecision(
                    source_record_id=record.id,
                    transaction_id=transaction_id,
                    action="created",
                    evidence=ReconciliationEvidence(),
                )
            )

        return ReconciliationResult(
            transactions=tuple(transactions_by_id[item] for item in ordered_transaction_ids),
            source_links=tuple(links),
            decisions=tuple(decisions),
        )

    @staticmethod
    def _authoritative_transaction(record: SourceRecord[Any], transaction_id: str) -> Transaction:
        """Copy only core financial facts because provenance and description must remain owned by Source Record."""
        return Transaction(
            id=transaction_id,
            transaction_type=record.transaction_type,
            transaction_date=record.transaction_date,
            amount=record.amount,
            currency=record.currency,
        )
