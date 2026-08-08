from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from family_spending.enrichment import TransactionEnrichment
from family_spending.source_records import SourceRecord
from family_spending.transactions import Transaction

ZERO = Decimal("0")
MERCHANT_REFUND_LOOKBACK_DAYS = 30


class RefundReconciliationError(RuntimeError):
    """Raised when expense Transactions cannot be netted without violating source or amount invariants."""


@dataclass
class _ConsumptionBalance:
    original_index: int
    transaction: Transaction
    description: str | None
    merchant_name: str | None
    original_spending: Decimal
    remaining_spending: Decimal


@dataclass(frozen=True)
class NetConsumption:
    transaction_id: str
    spending: Decimal


@dataclass(frozen=True)
class RefundReconciliationResult:
    net_consumption: tuple[NetConsumption, ...]
    zero_amount_transactions: int
    refund_transactions: int
    same_merchant_refund_matches: int
    same_merchant_matched_amount: Decimal
    fully_refunded_transactions: int
    partially_refunded_transactions: int
    unmatched_refund_count: int
    unmatched_refund_amount: Decimal


def _find_exact_balance(
    balances: list[_ConsumptionBalance],
    refund_amount: Decimal,
) -> _ConsumptionBalance | None:
    """Prefer the latest exact remaining balance because it is the most conservative same-description match."""
    return next(
        (
            balance
            for balance in reversed(balances)
            if balance.remaining_spending == refund_amount
            and balance.remaining_spending > ZERO
        ),
        None,
    )


def _find_same_merchant_balance(
    balances: list[_ConsumptionBalance],
    refund: Transaction,
    refund_amount: Decimal,
) -> _ConsumptionBalance | None:
    """Limit Merchant fallback to recent equal amounts so Merchant enrichment remains supporting evidence only."""
    for balance in reversed(balances):
        age_days = (refund.transaction_date - balance.transaction.transaction_date).days
        if age_days > MERCHANT_REFUND_LOOKBACK_DAYS:
            break
        if (
            balance.remaining_spending == refund_amount
            and balance.remaining_spending > ZERO
        ):
            return balance
    return None


def reconcile_refunds(
    transactions: tuple[Transaction, ...],
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]],
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment],
) -> RefundReconciliationResult:
    """Net refunds into a derived spending view while leaving authoritative Transaction amounts unchanged."""
    balances_by_description: dict[str | None, list[_ConsumptionBalance]] = {}
    balances_by_merchant: dict[str, list[_ConsumptionBalance]] = {}
    all_balances: list[_ConsumptionBalance] = []
    zero_amount_transactions = 0
    refund_transactions = 0
    same_merchant_refund_matches = 0
    same_merchant_matched_amount = ZERO
    unmatched_refund_count = 0
    unmatched_refund_amount = ZERO

    ordered_transactions = sorted(
        enumerate(transactions),
        key=lambda item: (item[1].transaction_date, item[0]),
    )
    for original_index, transaction in ordered_transactions:
        if transaction.transaction_type != "expense":
            raise RefundReconciliationError(
                f"Refund reconciliation currently accepts expense Transactions only: {transaction.id!r}"
            )
        try:
            source_record = source_records_by_transaction_id[transaction.id]
        except KeyError as exc:
            raise RefundReconciliationError(
                f"Transaction {transaction.id!r} has no authoritative Source Record"
            ) from exc
        try:
            enrichment = enrichments_by_transaction_id[transaction.id]
        except KeyError as exc:
            raise RefundReconciliationError(
                f"Transaction {transaction.id!r} has no current Enrichment"
            ) from exc

        amount = transaction.amount
        if amount == ZERO:
            zero_amount_transactions += 1
            continue

        description = source_record.description
        description_balances = balances_by_description.setdefault(description, [])
        merchant_name = enrichment.merchant_name
        if amount > ZERO:
            balance = _ConsumptionBalance(
                original_index=original_index,
                transaction=transaction,
                description=description,
                merchant_name=merchant_name,
                original_spending=amount,
                remaining_spending=amount,
            )
            description_balances.append(balance)
            if merchant_name is not None:
                balances_by_merchant.setdefault(merchant_name, []).append(balance)
            all_balances.append(balance)
            continue

        refund_transactions += 1
        remaining_refund = -amount
        exact_balance = _find_exact_balance(description_balances, remaining_refund)
        if exact_balance is not None:
            exact_balance.remaining_spending = ZERO
            remaining_refund = ZERO
        else:
            same_merchant_balance = None
            if merchant_name is not None:
                same_merchant_balance = _find_same_merchant_balance(
                    balances_by_merchant.get(merchant_name, []),
                    transaction,
                    remaining_refund,
                )
            if same_merchant_balance is not None:
                matched_amount = remaining_refund
                same_merchant_balance.remaining_spending = ZERO
                remaining_refund = ZERO
                same_merchant_refund_matches += 1
                same_merchant_matched_amount += matched_amount
            else:
                for balance in reversed(description_balances):
                    if balance.remaining_spending <= ZERO:
                        continue
                    refunded_amount = min(
                        balance.remaining_spending,
                        remaining_refund,
                    )
                    balance.remaining_spending -= refunded_amount
                    remaining_refund -= refunded_amount
                    if remaining_refund == ZERO:
                        break
        if remaining_refund > ZERO:
            unmatched_refund_count += 1
            unmatched_refund_amount += remaining_refund

    fully_refunded_transactions = sum(
        balance.remaining_spending == ZERO for balance in all_balances
    )
    partially_refunded_transactions = sum(
        ZERO < balance.remaining_spending < balance.original_spending
        for balance in all_balances
    )
    net_consumption = tuple(
        NetConsumption(
            transaction_id=balance.transaction.id,
            spending=balance.remaining_spending,
        )
        for balance in sorted(all_balances, key=lambda item: item.original_index)
        if balance.remaining_spending > ZERO
    )
    return RefundReconciliationResult(
        net_consumption=net_consumption,
        zero_amount_transactions=zero_amount_transactions,
        refund_transactions=refund_transactions,
        same_merchant_refund_matches=same_merchant_refund_matches,
        same_merchant_matched_amount=same_merchant_matched_amount,
        fully_refunded_transactions=fully_refunded_transactions,
        partially_refunded_transactions=partially_refunded_transactions,
        unmatched_refund_count=unmatched_refund_count,
        unmatched_refund_amount=unmatched_refund_amount,
    )
