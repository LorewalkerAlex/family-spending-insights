from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Generic, TypeVar

from family_spending.enrichment import TransactionEnrichment
from family_spending.refund_reconciliation import NetConsumption
from family_spending.transactions import Transaction

ZERO = Decimal("0")
AnalyticsResultT = TypeVar("AnalyticsResultT")


class SpendingStatisticsError(RuntimeError):
    """Raised when net consumption and current domain state cannot reconcile into consistent statistics."""


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


class AnalyticsProcessor(ABC, Generic[AnalyticsResultT]):
    @abstractmethod
    def process(
        self,
        net_consumption: tuple[NetConsumption, ...],
        transactions_by_id: Mapping[str, Transaction],
        enrichments_by_transaction_id: Mapping[str, TransactionEnrichment],
    ) -> AnalyticsResultT:
        """Keep downstream analytics replaceable without making core Transaction or Enrichment depend on reports."""


@dataclass
class _Aggregate:
    spending: Decimal = ZERO
    transaction_count: int = 0

    def add(self, spending: Decimal) -> None:
        """Update amount and count together because every surviving NetConsumption represents exactly one purchase."""
        self.spending += spending
        self.transaction_count += 1


@dataclass
class _MonthAggregate:
    total: _Aggregate
    categories: dict[str, _Aggregate]
    merchants: dict[tuple[str | None, str, bool], _Aggregate]


def _new_month_aggregate() -> _MonthAggregate:
    """Create isolated mutable buckets so one month's accumulation can never leak into another month."""
    return _MonthAggregate(
        total=_Aggregate(),
        categories={},
        merchants={},
    )


class SpendingStatisticsProcessor(AnalyticsProcessor[SpendingStatistics]):
    def process(
        self,
        net_consumption: tuple[NetConsumption, ...],
        transactions_by_id: Mapping[str, Transaction],
        enrichments_by_transaction_id: Mapping[str, TransactionEnrichment],
    ) -> SpendingStatistics:
        """Join current Enrichment at analysis time so edits never require rewriting Transactions."""
        by_month: dict[str, _MonthAggregate] = {}
        for consumption in net_consumption:
            if consumption.spending <= ZERO:
                raise SpendingStatisticsError(
                    "Statistics input must contain only positive net consumption spending: "
                    f"transaction_id={consumption.transaction_id!r}, "
                    f"spending={format(consumption.spending, 'f')}"
                )
            try:
                transaction = transactions_by_id[consumption.transaction_id]
            except KeyError as exc:
                raise SpendingStatisticsError(
                    f"Net consumption references missing transaction {consumption.transaction_id!r}"
                ) from exc
            try:
                enrichment = enrichments_by_transaction_id[consumption.transaction_id]
            except KeyError as exc:
                raise SpendingStatisticsError(
                    f"Net consumption references missing enrichment {consumption.transaction_id!r}"
                ) from exc
            month = transaction.transaction_date.strftime("%Y-%m")
            month_aggregate = by_month.setdefault(month, _new_month_aggregate())
            month_aggregate.total.add(consumption.spending)
            month_aggregate.categories.setdefault(
                enrichment.category,
                _Aggregate(),
            ).add(consumption.spending)
            # Merchant identity can be known while Category remains unclassified, and vice versa.
            merchant_key = (
                enrichment.merchant_name,
                enrichment.display_name,
                enrichment.merchant_name is None,
            )
            month_aggregate.merchants.setdefault(merchant_key, _Aggregate()).add(
                consumption.spending
            )
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


def aggregate_spending(
    net_consumption: tuple[NetConsumption, ...],
    transactions_by_id: Mapping[str, Transaction],
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment],
) -> SpendingStatistics:
    """Retain a small functional entrypoint so callers need not know which AnalyticsProcessor implements spending v1."""
    return SpendingStatisticsProcessor().process(
        net_consumption,
        transactions_by_id,
        enrichments_by_transaction_id,
    )
