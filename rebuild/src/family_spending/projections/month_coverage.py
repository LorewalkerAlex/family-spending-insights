from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

STATEMENT_DAY = 10
MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")


class MonthCoverageError(RuntimeError):
    """Raised when month labels or statement evidence cannot form deterministic coverage."""


@dataclass(frozen=True)
class MonthCoverage:
    """Derived natural-month completeness used by current projection visibility rules."""

    month: str
    is_complete: bool
    show: bool


def _parse_month(value: str) -> tuple[int, int]:
    match = MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise MonthCoverageError(f"Invalid projection month {value!r}; expected YYYY-MM")
    year = int(match.group("year"))
    month = int(match.group("month"))
    if month < 1 or month > 12:
        raise MonthCoverageError(f"Invalid projection month {value!r}; expected YYYY-MM")
    return year, month


def _next_month(year: int, month: int) -> tuple[int, int]:
    """Advance one calendar month without depending on timezone or wall-clock state."""
    if month == 12:
        return year + 1, 1
    return year, month + 1


def build_month_coverage(
    months: tuple[str, ...],
    statement_dates: frozenset[date],
) -> tuple[MonthCoverage, ...]:
    """Derive completeness from statement dates, never from legacy evidence filenames."""
    seen: set[str] = set()
    coverage: list[MonthCoverage] = []
    for month_name in months:
        if month_name in seen:
            raise MonthCoverageError(f"Duplicate projection month {month_name!r}")
        seen.add(month_name)
        year, month = _parse_month(month_name)
        next_year, next_month = _next_month(year, month)
        required = (
            date(year, month, STATEMENT_DAY),
            date(next_year, next_month, STATEMENT_DAY),
        )
        is_complete = all(item in statement_dates for item in required)
        coverage.append(MonthCoverage(month_name, is_complete, is_complete))
    return tuple(coverage)
