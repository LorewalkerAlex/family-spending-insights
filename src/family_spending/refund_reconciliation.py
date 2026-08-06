from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal

from family_spending.ingestion.cmb_email_transactions import CmbTransaction

ZERO = Decimal("0")
MERCHANT_REFUND_LOOKBACK_DAYS = 30


class RefundReconciliationError(RuntimeError):
    """Raised when raw transaction amounts cannot be reconciled safely."""


@dataclass
class _ConsumptionBalance:
    original_index: int
    transaction: CmbTransaction
    original_spending: Decimal
    remaining_spending: Decimal


@dataclass(frozen=True)
class RefundReconciliationResult:
    net_transactions: tuple[CmbTransaction, ...]
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
    refund: CmbTransaction,
    refund_amount: Decimal,
) -> _ConsumptionBalance | None:
    for balance in reversed(balances):
        age_days = (
            refund.transaction_date - balance.transaction.transaction_date
        ).days
        if age_days > MERCHANT_REFUND_LOOKBACK_DAYS:
            break
        if (
            balance.remaining_spending == refund_amount
            and balance.remaining_spending > ZERO
        ):
            return balance
    return None


def reconcile_refunds(
    transactions: tuple[CmbTransaction, ...],
    description_to_merchant: Mapping[str, str] | None = None,
) -> RefundReconciliationResult:
    """Apply refunds to prior consumption using conservative matching rules.

    Raw CMB amounts use positive values for consumption and negative values
    for refunds. Matching first prefers an equal remaining balance under the
    exact description. If none exists, an equal remaining balance under the
    same confirmed merchant may be used when it occurred within the previous
    30 calendar days. Only then may the refund accumulate across older balances
    with the exact description. Transactions are processed chronologically,
    using the original tuple position as the stable same-day tie-breaker.
    Output transactions keep the original consumption identity and use
    normalized negative net amounts.
    """
    merchant_lookup = description_to_merchant or {}
    balances_by_description: dict[str, list[_ConsumptionBalance]] = {}
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
        amount = transaction.amount
        if amount == ZERO:
            zero_amount_transactions += 1
            continue

        description_balances = balances_by_description.setdefault(
            transaction.description,
            [],
        )
        merchant_name = merchant_lookup.get(transaction.description)

        if amount > ZERO:
            spending = amount
            balance = _ConsumptionBalance(
                original_index=original_index,
                transaction=transaction,
                original_spending=spending,
                remaining_spending=spending,
            )
            description_balances.append(balance)
            if merchant_name is not None:
                balances_by_merchant.setdefault(merchant_name, []).append(balance)
            all_balances.append(balance)
            continue

        refund_transactions += 1
        remaining_refund = -amount

        exact_balance = _find_exact_balance(
            description_balances,
            remaining_refund,
        )
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

    net_transactions: list[CmbTransaction] = []
    for balance in sorted(all_balances, key=lambda item: item.original_index):
        if balance.remaining_spending == ZERO:
            continue
        net_transactions.append(
            replace(
                balance.transaction,
                amount=-balance.remaining_spending,
            )
        )

    return RefundReconciliationResult(
        net_transactions=tuple(net_transactions),
        zero_amount_transactions=zero_amount_transactions,
        refund_transactions=refund_transactions,
        same_merchant_refund_matches=same_merchant_refund_matches,
        same_merchant_matched_amount=same_merchant_matched_amount,
        fully_refunded_transactions=fully_refunded_transactions,
        partially_refunded_transactions=partially_refunded_transactions,
        unmatched_refund_count=unmatched_refund_count,
        unmatched_refund_amount=unmatched_refund_amount,
    )
