from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from family_spending.domain.transaction import Transaction
from family_spending.projections.month_coverage import build_month_coverage
from family_spending.projections.spending import SpendingStatistics

FINANCIAL_SUMMARY_SCHEMA_VERSION = 1
MINOR_UNIT_SCALE = Decimal("100")
ZERO = Decimal("0")


class FinancialProjectionError(RuntimeError):
    """Raised when income and net spending cannot form an exact financial projection."""


@dataclass(frozen=True)
class FinancialProjection:
    """Derived financial payload compatible with the current public summary schema."""

    payload: dict[str, object]


def _to_signed_minor_units(value: Decimal, label: str) -> int:
    if not value.is_finite():
        raise FinancialProjectionError(f"{label} must be finite, got {value!r}")
    minor = value * MINOR_UNIT_SCALE
    integral = minor.to_integral_value()
    if minor != integral:
        raise FinancialProjectionError(f"{label} has more than two decimal places: {value!r}")
    return int(integral)


def _to_non_negative_minor_units(value: Decimal, label: str) -> int:
    if value < ZERO:
        raise FinancialProjectionError(f"{label} must be non-negative, got {value!r}")
    return _to_signed_minor_units(value, label)


def _summary(rows: list[dict[str, object]]) -> dict[str, int]:
    total_income = sum(int(row["total_income_minor"]) for row in rows)
    total_spending = sum(int(row["total_spending_minor"]) for row in rows)
    return {
        "total_income_minor": total_income,
        "total_spending_minor": total_spending,
        "net_cash_flow_minor": total_income - total_spending,
        "income_transaction_count": sum(int(row["income_transaction_count"]) for row in rows),
        "spending_transaction_count": sum(int(row["spending_transaction_count"]) for row in rows),
        "month_count": len(rows),
    }


def build_financial_projection(
    transactions: tuple[Transaction, ...],
    spending_statistics: SpendingStatistics,
    statement_dates: frozenset[date],
) -> FinancialProjection:
    """Build income, spending, and cash-flow views from canonical Transactions plus net spending."""
    spending_by_month = {item.month: item for item in spending_statistics.months}
    income_by_month: dict[str, Decimal] = {}
    income_count_by_month: dict[str, int] = {}
    for transaction in transactions:
        if transaction.transaction_type != "income":
            continue
        if transaction.amount <= ZERO:
            raise FinancialProjectionError(
                f"Income Transaction {transaction.id!r} must have positive amount, got {transaction.amount!r}"
            )
        month = transaction.transaction_date.strftime("%Y-%m")
        income_by_month[month] = income_by_month.get(month, ZERO) + transaction.amount
        income_count_by_month[month] = income_count_by_month.get(month, 0) + 1

    month_names = tuple(sorted(set(spending_by_month) | set(income_by_month), reverse=True))
    coverage_by_month = {
        item.month: item for item in build_month_coverage(month_names, statement_dates)
    }
    rows: list[dict[str, object]] = []
    for month_name in month_names:
        spending = spending_by_month.get(month_name)
        total_spending = spending.total_spending if spending is not None else ZERO
        spending_count = spending.transaction_count if spending is not None else 0
        total_income = income_by_month.get(month_name, ZERO)
        income_count = income_count_by_month.get(month_name, 0)
        coverage = coverage_by_month[month_name]
        rows.append(
            {
                "month": month_name,
                "spending_data_complete": coverage.is_complete,
                "show": coverage.show,
                "total_income_minor": _to_non_negative_minor_units(total_income, f"income {month_name}"),
                "income_transaction_count": income_count,
                "total_spending_minor": _to_non_negative_minor_units(total_spending, f"spending {month_name}"),
                "spending_transaction_count": spending_count,
                "net_cash_flow_minor": _to_signed_minor_units(
                    total_income - total_spending,
                    f"cash flow {month_name}",
                ),
            }
        )

    all_summary = _summary(rows)
    shown_rows = [row for row in rows if bool(row["show"])]
    shown_summary = _summary(shown_rows)

    expected_total_income = sum(income_by_month.values(), start=ZERO)
    expected = {
        "total_income_minor": _to_non_negative_minor_units(expected_total_income, "total income"),
        "total_spending_minor": _to_non_negative_minor_units(
            spending_statistics.total_spending,
            "total spending",
        ),
        "net_cash_flow_minor": _to_signed_minor_units(
            expected_total_income - spending_statistics.total_spending,
            "net cash flow",
        ),
        "income_transaction_count": sum(income_count_by_month.values()),
        "spending_transaction_count": spending_statistics.transaction_count,
        "month_count": len(rows),
    }
    if all_summary != expected:
        raise FinancialProjectionError("Financial all-data summary does not reconcile")

    return FinancialProjection(
        {
            "schema_version": FINANCIAL_SUMMARY_SCHEMA_VERSION,
            "summary": {"all_data": all_summary, "shown_data": shown_summary},
            "months": rows,
        }
    )
