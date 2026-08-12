from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from family_spending.enrichment import TransactionEnrichment
from family_spending.month_coverage import load_month_coverage
from family_spending.refund_reconciliation import reconcile_refunds
from family_spending.source_records import SourceRecord
from family_spending.spending_statistics import aggregate_spending
from family_spending.transactions import Transaction

FINANCIAL_SUMMARY_SCHEMA_VERSION = 1
MINOR_UNIT_SCALE = Decimal("100")
ZERO = Decimal("0")


class FinancialProjectionError(RuntimeError):
    """Raised when household income and net spending cannot form a consistent projection."""


@dataclass(frozen=True)
class FinancialProjection:
    payload: dict[str, Any]


def _to_non_negative_minor_units(value: Decimal, label: str) -> int:
    if not value.is_finite() or value < ZERO:
        raise FinancialProjectionError(f"{label} must be finite and non-negative, got {value!r}")
    return _to_signed_minor_units(value, label)


def _to_signed_minor_units(value: Decimal, label: str) -> int:
    if not value.is_finite():
        raise FinancialProjectionError(f"{label} must be finite, got {value!r}")
    minor_units = value * MINOR_UNIT_SCALE
    integral_minor_units = minor_units.to_integral_value()
    if minor_units != integral_minor_units:
        raise FinancialProjectionError(f"{label} has more than two decimal places: {value!r}")
    return int(integral_minor_units)


def _summary_payload(
    *,
    total_income: Decimal,
    total_spending: Decimal,
    income_transaction_count: int,
    spending_transaction_count: int,
    month_count: int,
) -> dict[str, int]:
    return {
        "total_income_minor": _to_non_negative_minor_units(total_income, "total income"),
        "total_spending_minor": _to_non_negative_minor_units(total_spending, "total spending"),
        "net_cash_flow_minor": _to_signed_minor_units(
            total_income - total_spending,
            "net cash flow",
        ),
        "income_transaction_count": income_transaction_count,
        "spending_transaction_count": spending_transaction_count,
        "month_count": month_count,
    }


def build_financial_projection(
    transactions: tuple[Transaction, ...],
    transactions_by_id: Mapping[str, Transaction],
    source_records_by_transaction_id: Mapping[str, SourceRecord[Any]],
    enrichments_by_transaction_id: Mapping[str, TransactionEnrichment],
    emails_dir: Path,
) -> FinancialProjection:
    """Build income, net-spending, and net-cash-flow views without changing source or transaction facts."""
    refund_result = reconcile_refunds(
        transactions,
        source_records_by_transaction_id,
        enrichments_by_transaction_id,
    )
    spending_statistics = aggregate_spending(
        refund_result.net_consumption,
        transactions_by_id,
        enrichments_by_transaction_id,
    )
    spending_by_month = {month.month: month for month in spending_statistics.months}

    income_by_month: dict[str, Decimal] = {}
    income_count_by_month: dict[str, int] = {}
    for transaction in transactions:
        if transaction.transaction_type != "income":
            continue
        if transaction.amount <= ZERO:
            raise FinancialProjectionError(
                "Income transactions must have positive amounts: "
                f"transaction_id={transaction.id!r}, amount={format(transaction.amount, 'f')}"
            )
        month = transaction.transaction_date.strftime("%Y-%m")
        income_by_month[month] = income_by_month.get(month, ZERO) + transaction.amount
        income_count_by_month[month] = income_count_by_month.get(month, 0) + 1

    month_names = tuple(
        sorted(set(spending_by_month) | set(income_by_month), reverse=True)
    )
    month_coverage = load_month_coverage(month_names, emails_dir)
    coverage_by_month = {item.month: item for item in month_coverage}

    months: list[dict[str, object]] = []
    for month_name in month_names:
        spending = spending_by_month.get(month_name)
        total_spending = spending.total_spending if spending is not None else ZERO
        spending_count = spending.transaction_count if spending is not None else 0
        total_income = income_by_month.get(month_name, ZERO)
        income_count = income_count_by_month.get(month_name, 0)
        coverage = coverage_by_month[month_name]
        months.append(
            {
                "month": month_name,
                "spending_data_complete": coverage.is_complete,
                "show": coverage.show,
                "total_income_minor": _to_non_negative_minor_units(
                    total_income,
                    f"income for {month_name}",
                ),
                "income_transaction_count": income_count,
                "total_spending_minor": _to_non_negative_minor_units(
                    total_spending,
                    f"spending for {month_name}",
                ),
                "spending_transaction_count": spending_count,
                "net_cash_flow_minor": _to_signed_minor_units(
                    total_income - total_spending,
                    f"net cash flow for {month_name}",
                ),
            }
        )

    def summarize(selected: list[dict[str, object]]) -> dict[str, int]:
        total_income_minor = sum(int(item["total_income_minor"]) for item in selected)
        total_spending_minor = sum(int(item["total_spending_minor"]) for item in selected)
        net_cash_flow_minor = total_income_minor - total_spending_minor
        return {
            "total_income_minor": total_income_minor,
            "total_spending_minor": total_spending_minor,
            "net_cash_flow_minor": net_cash_flow_minor,
            "income_transaction_count": sum(
                int(item["income_transaction_count"]) for item in selected
            ),
            "spending_transaction_count": sum(
                int(item["spending_transaction_count"]) for item in selected
            ),
            "month_count": len(selected),
        }

    all_summary = summarize(months)
    shown_months = [item for item in months if bool(item["show"])]
    shown_summary = summarize(shown_months)

    # Rebuild the same values through Decimal helpers once so the public payload cannot
    # bypass amount precision checks merely because month rows were already serialized.
    expected_all_summary = _summary_payload(
        total_income=sum(income_by_month.values(), start=ZERO),
        total_spending=spending_statistics.total_spending,
        income_transaction_count=sum(income_count_by_month.values()),
        spending_transaction_count=spending_statistics.transaction_count,
        month_count=len(months),
    )
    if all_summary != expected_all_summary:
        raise FinancialProjectionError("Financial all-data summary does not reconcile")

    return FinancialProjection(
        payload={
            "schema_version": FINANCIAL_SUMMARY_SCHEMA_VERSION,
            "summary": {
                "all_data": all_summary,
                "shown_data": shown_summary,
            },
            "months": months,
        }
    )


def encode_financial_projection(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_financial_projection(
    projection: FinancialProjection,
    output_path: Path,
) -> None:
    """Atomically replace the derived financial summary sidecar."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = encode_financial_projection(projection.payload)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(encoded)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, output_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
