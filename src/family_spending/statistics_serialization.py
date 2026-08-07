from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from family_spending.month_coverage import MonthCoverage
from family_spending.settings import SPENDING_STATISTICS_FILE
from family_spending.spending_statistics import SpendingStatistics

STATISTICS_SCHEMA_VERSION = 2
MINOR_UNIT_SCALE = Decimal("100")
ZERO = Decimal("0")


class StatisticsSerializationError(RuntimeError):
    """Raised when statistics cannot be represented by the public schema."""


def _to_minor_units(value: Decimal) -> int:
    if not value.is_finite() or value < ZERO:
        raise StatisticsSerializationError(
            f"Spending amount must be finite and non-negative, got {value!r}"
        )
    minor_units = value * MINOR_UNIT_SCALE
    integral_minor_units = minor_units.to_integral_value()
    if minor_units != integral_minor_units:
        raise StatisticsSerializationError(
            f"Spending amount has more than two decimal places: {value!r}"
        )
    return int(integral_minor_units)


def _summary_payload(
    total_spending: Decimal,
    transaction_count: int,
    month_count: int,
) -> dict[str, int]:
    return {
        "total_spending_minor": _to_minor_units(total_spending),
        "transaction_count": transaction_count,
        "month_count": month_count,
    }


def _index_month_coverage(
    statistics: SpendingStatistics,
    month_coverage: tuple[MonthCoverage, ...],
) -> dict[str, MonthCoverage]:
    coverage_by_month: dict[str, MonthCoverage] = {}
    for coverage in month_coverage:
        if coverage.month in coverage_by_month:
            raise StatisticsSerializationError(
                f"Duplicate month coverage for {coverage.month!r}"
            )
        coverage_by_month[coverage.month] = coverage

    statistics_months = {month.month for month in statistics.months}
    coverage_months = set(coverage_by_month)
    if coverage_months != statistics_months:
        missing = sorted(statistics_months - coverage_months)
        extra = sorted(coverage_months - statistics_months)
        raise StatisticsSerializationError(
            "Month coverage does not match statistics months: "
            f"missing={missing}, extra={extra}"
        )
    return coverage_by_month


def serialize_spending_statistics(
    statistics: SpendingStatistics,
    month_coverage: tuple[MonthCoverage, ...],
) -> dict[str, object]:
    coverage_by_month = _index_month_coverage(statistics, month_coverage)
    shown_months = tuple(
        month
        for month in statistics.months
        if coverage_by_month[month.month].show
    )
    shown_total_spending = sum(
        (month.total_spending for month in shown_months),
        start=ZERO,
    )
    shown_transaction_count = sum(
        month.transaction_count for month in shown_months
    )

    return {
        "schema_version": STATISTICS_SCHEMA_VERSION,
        "summary": {
            "all_data": _summary_payload(
                statistics.total_spending,
                statistics.transaction_count,
                len(statistics.months),
            ),
            "shown_data": _summary_payload(
                shown_total_spending,
                shown_transaction_count,
                len(shown_months),
            ),
        },
        "months": [
            {
                "month": month.month,
                "is_complete": coverage_by_month[month.month].is_complete,
                "show": coverage_by_month[month.month].show,
                "total_spending_minor": _to_minor_units(month.total_spending),
                "transaction_count": month.transaction_count,
                "categories": [
                    {
                        "category": category.category,
                        "spending_minor": _to_minor_units(category.spending),
                        "transaction_count": category.transaction_count,
                    }
                    for category in month.categories
                ],
                "merchants": [
                    {
                        "merchant_name": merchant.merchant_name,
                        "display_name": merchant.display_name,
                        "is_unclassified": merchant.is_unclassified,
                        "spending_minor": _to_minor_units(merchant.spending),
                        "transaction_count": merchant.transaction_count,
                    }
                    for merchant in month.merchants
                ],
            }
            for month in statistics.months
        ],
    }


def encode_spending_statistics(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def write_spending_statistics_json(
    payload: dict[str, object],
    output_path: Path = SPENDING_STATISTICS_FILE,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = encode_spending_statistics(payload)
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
