from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from family_spending.source_records import SourceRecord
from family_spending.transactions import (
    Transaction,
    TransactionSourceLink,
    build_transaction_id,
    index_transactions,
    transaction_from_source_record,
)

ReconciliationAction = Literal["created", "reused", "matched"]
CROSS_SOURCE_MATCH_WINDOW_DAYS = 3


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


@dataclass(frozen=True)
class ReconciliationContext:
    """Provide current read-only evidence without making it part of Transaction identity.

    Merchant information is intentionally optional and Category is intentionally absent.
    Source Records are included only so source-aware reconcilers can distinguish the
    authority of an existing Transaction without copying source metadata into Transaction.
    """

    source_records_by_id: Mapping[str, SourceRecord[Any]]
    merchant_by_transaction_id: Mapping[str, str | None]
    merchant_by_source_record_id: Mapping[str, str | None]

    @classmethod
    def empty(cls) -> ReconciliationContext:
        empty: Mapping[str, Any] = MappingProxyType({})
        return cls(
            source_records_by_id=empty,
            merchant_by_transaction_id=empty,
            merchant_by_source_record_id=empty,
        )


@dataclass(frozen=True)
class _Candidate:
    transaction: Transaction
    evidence: ReconciliationEvidence


class Reconciler(ABC):
    @abstractmethod
    def reconcile(
        self,
        records: tuple[SourceRecord[Any], ...],
        *,
        existing_transactions: tuple[Transaction, ...] = (),
        existing_links: tuple[TransactionSourceLink, ...] = (),
        context: ReconciliationContext | None = None,
    ) -> ReconciliationResult:
        """Resolve source identity into system identity while keeping source-specific policy behind this boundary."""


def _build_link_index(
    links: tuple[TransactionSourceLink, ...],
) -> dict[str, TransactionSourceLink]:
    """Require one Transaction relation per Source Record so reruns cannot silently change identity."""
    indexed: dict[str, TransactionSourceLink] = {}
    for link in links:
        if link.source_record_id in indexed:
            raise ReconciliationError(
                f"Source record {link.source_record_id!r} is linked more than once"
            )
        indexed[link.source_record_id] = link
    return indexed


def _candidate_for(
    record: SourceRecord[Any],
    transaction: Transaction,
    *,
    record_merchant: str | None,
    transaction_merchant: str | None,
) -> _Candidate | None:
    """Keep cross-source matching conservative and explainable with independent evidence signals."""
    if record.transaction_type != transaction.transaction_type:
        return None
    if record.currency != transaction.currency:
        return None
    if record.amount != transaction.amount:
        return None

    date_distance_days = abs((record.transaction_date - transaction.transaction_date).days)
    if date_distance_days > CROSS_SOURCE_MATCH_WINDOW_DAYS:
        return None

    merchant_match: bool | None = None
    if record_merchant is not None and transaction_merchant is not None:
        merchant_match = record_merchant == transaction_merchant
        if not merchant_match:
            return None

    return _Candidate(
        transaction=transaction,
        evidence=ReconciliationEvidence(
            amount_match=True,
            date_distance_days=date_distance_days,
            merchant_match=merchant_match,
        ),
    )


def _select_candidate(
    candidates: list[_Candidate],
) -> tuple[_Candidate | None, bool]:
    """Return one candidate only when the current evidence can distinguish it without guessing."""
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0], False

    merchant_matches = [
        candidate for candidate in candidates if candidate.evidence.merchant_match is True
    ]
    if len(merchant_matches) == 1:
        return merchant_matches[0], False
    return None, True


def _set_authoritative_source(
    links: list[TransactionSourceLink],
    transaction_id: str,
    source_record_id: str,
) -> None:
    """Move authority to one source while preserving every older source as supporting evidence."""
    replaced = False
    updated: list[TransactionSourceLink] = []
    for link in links:
        if link.transaction_id != transaction_id:
            updated.append(link)
            continue
        if link.source_record_id == source_record_id:
            updated.append(
                TransactionSourceLink(
                    transaction_id=transaction_id,
                    source_record_id=source_record_id,
                    role="authoritative",
                )
            )
            replaced = True
            continue
        updated.append(
            TransactionSourceLink(
                transaction_id=link.transaction_id,
                source_record_id=link.source_record_id,
                role="supporting" if link.role == "authoritative" else link.role,
            )
        )
    if not replaced:
        updated.append(
            TransactionSourceLink(
                transaction_id=transaction_id,
                source_record_id=source_record_id,
                role="authoritative",
            )
        )
    links[:] = updated


def _add_supporting_link(
    links: list[TransactionSourceLink],
    transaction_id: str,
    source_record_id: str,
) -> None:
    """Attach another source to an existing Transaction without changing its current authority."""
    links.append(
        TransactionSourceLink(
            transaction_id=transaction_id,
            source_record_id=source_record_id,
            role="supporting",
        )
    )


class ManualReconciler(Reconciler):
    SOURCE_TYPE = "manual"

    def reconcile(
        self,
        records: tuple[SourceRecord[Any], ...],
        *,
        existing_transactions: tuple[Transaction, ...] = (),
        existing_links: tuple[TransactionSourceLink, ...] = (),
        context: ReconciliationContext | None = None,
    ) -> ReconciliationResult:
        """Deduplicate Manual Source before creation because manual input is not authoritative over existing facts."""
        context = context or ReconciliationContext.empty()
        transactions_by_id = dict(index_transactions(existing_transactions))
        ordered_transaction_ids = [transaction.id for transaction in existing_transactions]
        links = list(existing_links)
        link_by_source_record = _build_link_index(existing_links)
        merchant_by_transaction_id = dict(context.merchant_by_transaction_id)

        seen_records: set[str] = set()
        decisions: list[ReconciliationDecision] = []
        for record in records:
            if record.source_type != self.SOURCE_TYPE:
                raise ReconciliationError(
                    f"ManualReconciler cannot process source type {record.source_type!r}"
                )
            if record.id in seen_records:
                raise ReconciliationError(f"Duplicate Manual source record {record.id!r}")
            seen_records.add(record.id)

            existing_link = link_by_source_record.get(record.id)
            if existing_link is not None:
                if existing_link.transaction_id not in transactions_by_id:
                    raise ReconciliationError(
                        f"Source record {record.id!r} links to missing transaction {existing_link.transaction_id!r}"
                    )
                decisions.append(
                    ReconciliationDecision(
                        source_record_id=record.id,
                        transaction_id=existing_link.transaction_id,
                        action="reused",
                        evidence=ReconciliationEvidence(source_identity_match=True),
                    )
                )
                continue

            record_merchant = context.merchant_by_source_record_id.get(record.id)
            candidates: list[_Candidate] = []
            for transaction in transactions_by_id.values():
                candidate = _candidate_for(
                    record,
                    transaction,
                    record_merchant=record_merchant,
                    transaction_merchant=merchant_by_transaction_id.get(transaction.id),
                )
                if candidate is not None:
                    candidates.append(candidate)
            selected, ambiguous = _select_candidate(candidates)
            if ambiguous:
                candidate_ids = sorted(candidate.transaction.id for candidate in candidates)
                raise ReconciliationError(
                    f"Manual source record {record.id!r} matches multiple existing transactions: {candidate_ids!r}"
                )

            if selected is not None:
                transaction_id = selected.transaction.id
                _add_supporting_link(links, transaction_id, record.id)
                link_by_source_record[record.id] = links[-1]
                if merchant_by_transaction_id.get(transaction_id) is None and record_merchant is not None:
                    merchant_by_transaction_id[transaction_id] = record_merchant
                decisions.append(
                    ReconciliationDecision(
                        source_record_id=record.id,
                        transaction_id=transaction_id,
                        action="matched",
                        evidence=selected.evidence,
                    )
                )
                continue

            transaction_id = build_transaction_id(record)
            if transaction_id in transactions_by_id:
                raise ReconciliationError(
                    f"Generated transaction id {transaction_id!r} already exists for another source record"
                )
            transaction = transaction_from_source_record(record, transaction_id)
            transactions_by_id[transaction_id] = transaction
            ordered_transaction_ids.append(transaction_id)
            link = TransactionSourceLink(
                transaction_id=transaction_id,
                source_record_id=record.id,
                role="authoritative",
            )
            links.append(link)
            link_by_source_record[record.id] = link
            if record_merchant is not None:
                merchant_by_transaction_id[transaction_id] = record_merchant
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


class CmbReconciler(Reconciler):
    SOURCE_TYPE = "cmb_email"

    def reconcile(
        self,
        records: tuple[SourceRecord[Any], ...],
        *,
        existing_transactions: tuple[Transaction, ...] = (),
        existing_links: tuple[TransactionSourceLink, ...] = (),
        context: ReconciliationContext | None = None,
    ) -> ReconciliationResult:
        """Make CMB authoritative while reusing an unambiguous manual-only Transaction when one already exists."""
        context = context or ReconciliationContext.empty()
        transactions_by_id = dict(index_transactions(existing_transactions))
        ordered_transaction_ids = [transaction.id for transaction in existing_transactions]
        links = list(existing_links)
        link_by_source_record = _build_link_index(existing_links)

        authoritative_link_by_transaction: dict[str, TransactionSourceLink] = {}
        for link in existing_links:
            if link.role != "authoritative":
                continue
            if link.transaction_id in authoritative_link_by_transaction:
                raise ReconciliationError(
                    f"Transaction {link.transaction_id!r} has multiple authoritative source links"
                )
            authoritative_link_by_transaction[link.transaction_id] = link

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
                _set_authoritative_source(links, transaction.id, record.id)
                link_by_source_record = _build_link_index(tuple(links))
                authoritative_link_by_transaction[transaction.id] = link_by_source_record[record.id]
                transactions_by_id[transaction.id] = transaction_from_source_record(
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

            record_merchant = context.merchant_by_source_record_id.get(record.id)
            candidates: list[_Candidate] = []
            for transaction in transactions_by_id.values():
                authoritative_link = authoritative_link_by_transaction.get(transaction.id)
                if authoritative_link is None:
                    continue
                authoritative_source = context.source_records_by_id.get(
                    authoritative_link.source_record_id
                )
                if authoritative_source is None or authoritative_source.source_type != ManualReconciler.SOURCE_TYPE:
                    continue
                candidate = _candidate_for(
                    record,
                    transaction,
                    record_merchant=record_merchant,
                    transaction_merchant=context.merchant_by_transaction_id.get(transaction.id),
                )
                if candidate is not None:
                    candidates.append(candidate)

            selected, ambiguous = _select_candidate(candidates)
            if selected is not None and not ambiguous:
                transaction_id = selected.transaction.id
                _set_authoritative_source(links, transaction_id, record.id)
                link_by_source_record = _build_link_index(tuple(links))
                transactions_by_id[transaction_id] = transaction_from_source_record(
                    record,
                    transaction_id,
                )
                authoritative_link_by_transaction[transaction_id] = link_by_source_record[record.id]
                decisions.append(
                    ReconciliationDecision(
                        source_record_id=record.id,
                        transaction_id=transaction_id,
                        action="matched",
                        evidence=selected.evidence,
                    )
                )
                continue

            # CMB itself is authoritative, so ambiguous manual candidates must not block ingestion.
            # Creating a separate Transaction is safer than silently merging the wrong real-world event.
            transaction_id = build_transaction_id(record)
            if transaction_id in transactions_by_id:
                raise ReconciliationError(
                    f"Generated transaction id {transaction_id!r} already exists for another source record"
                )
            transaction = transaction_from_source_record(record, transaction_id)
            transactions_by_id[transaction_id] = transaction
            ordered_transaction_ids.append(transaction_id)
            link = TransactionSourceLink(
                transaction_id=transaction_id,
                source_record_id=record.id,
                role="authoritative",
            )
            links.append(link)
            link_by_source_record[record.id] = link
            authoritative_link_by_transaction[transaction_id] = link
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
