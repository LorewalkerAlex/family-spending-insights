from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from family_spending.mapping import ResolvedTransaction

ZERO = Decimal("0")


class SpendingStatisticsError(RuntimeError):
    """Raised when resolved transactions cannot form consistent statistics."""


@dataclass(frozen=True)
class CategoryStatistics:
    category: str
    spending: Decimal
    transaction_count: int


@dataclass(frozen=True)
class MerchantStatistics:
    merchant_name: str | None
    display_name: str
    is_unclassified: bool
    spending: Decimal
    transaction_count: int


@dataclass(frozen=True)
class MonthlySpendingStatistics:
    month: str
    total_spending: Decimal
    transaction_count: int
    categories: tuple[CategoryStatistics, ...]
    merchants: tuple[MerchantStatistics, ...]


@dataclass(frozen=True)
class SpendingStatistics:
    total_spending: Decimal
    transaction_count: int
    months: tuple[MonthlySpendingStatistics, ...]


@dataclass
class _Aggregate:
    spending: Decimal = ZERO
    transaction_count: int = 0

    def add(self, spending: Decimal) -> None:
        self.spending += spending
        self.transaction_count += 1


@dataclass
class _MonthAggregate:
    total: _Aggregate
    categories: dict[str, _Aggregate]
    merchants: dict[tuple[str | None, str, bool], _Aggregate]


def _new_month_aggregate() -> _MonthAggregate:
    return _MonthAggregate(
        total=_Aggregate(),
        categories={},
        merchants={},
    )


def aggregate_spending(
    transactions: tuple[ResolvedTransaction, ...],
) -> SpendingStatistics:
    """Aggregate net consumption by month, category, and merchant/display."""
    by_month: dict[str, _MonthAggregate] = {}

    for item in transactions:
        amount = item.transaction.amount
        if amount >= ZERO:
            raise SpendingStatisticsError(
                "Statistics input must contain only negative net consumption amounts: "
                f"transaction_id={item.transaction.transaction_id!r}, amount={format(amount, 'f')}"
            )
        spending = -amount
        month = item.transaction.transaction_date.strftime("%Y-%m")
        month_aggregate = by_month.setdefault(month, _new_month_aggregate())
        month_aggregate.total.add(spending)
        month_aggregate.categories.setdefault(item.category, _Aggregate()).add(spending)
        merchant_key = (
            item.merchant_name,
            item.display_name,
            item.is_unmatched,
        )
        month_aggregate.merchants.setdefault(merchant_key, _Aggregate()).add(spending)

    monthly_statistics: list[MonthlySpendingStatistics] = []
    for month in sorted(by_month, reverse=True):
        aggregate = by_month[month]
        categories = tuple(
            CategoryStatistics(
                category=category,
                spending=value.spending,
                transaction_count=value.transaction_count,
            )
            for category, value in sorted(
                aggregate.categories.items(),
                key=lambda item: (-item[1].spending, item[0]),
            )
        )
        merchants = tuple(
            MerchantStatistics(
                merchant_name=merchant_name,
                display_name=display_name,
                is_unclassified=is_unclassified,
                spending=value.spending,
                transaction_count=value.transaction_count,
            )
            for (merchant_name, display_name, is_unclassified), value in sorted(
                aggregate.merchants.items(),
                key=lambda item: (
                    -item[1].spending,
                    item[0][1],
                    item[0][0] or "",
                ),
            )
        )
        category_spending = sum(
            (item.spending for item in categories),
            start=ZERO,
        )
        merchant_spending = sum(
            (item.spending for item in merchants),
            start=ZERO,
        )
        category_count = sum(item.transaction_count for item in categories)
        merchant_count = sum(item.transaction_count for item in merchants)
        if (
            category_spending != aggregate.total.spending
            or merchant_spending != aggregate.total.spending
            or category_count != aggregate.total.transaction_count
            or merchant_count != aggregate.total.transaction_count
        ):
            raise SpendingStatisticsError(
                f"Monthly statistics do not reconcile for {month}"
            )
        monthly_statistics.append(
            MonthlySpendingStatistics(
                month=month,
                total_spending=aggregate.total.spending,
                transaction_count=aggregate.total.transaction_count,
                categories=categories,
                merchants=merchants,
            )
        )

    total_spending = sum(
        (month.total_spending for month in monthly_statistics),
        start=ZERO,
    )
    transaction_count = sum(
        month.transaction_count for month in monthly_statistics
    )
    return SpendingStatistics(
        total_spending=total_spending,
        transaction_count=transaction_count,
        months=tuple(monthly_statistics),
    )
