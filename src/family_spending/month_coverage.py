from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

STATEMENT_DAY = 10
STATEMENT_FILENAME_PATTERN = re.compile(
    r"^(?P<mail_date>\d{4}-\d{2}-\d{2})_(?P<digest>[0-9a-f]{24})\.eml$"
)
MONTH_PATTERN = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")


class MonthCoverageError(RuntimeError):
    """Raised when statement filenames cannot establish month coverage."""


@dataclass(frozen=True)
class MonthCoverage:
    month: str
    is_complete: bool
    show: bool


def _parse_month(value: str) -> tuple[int, int]:
    match = MONTH_PATTERN.fullmatch(value)
    if match is None:
        raise MonthCoverageError(f"Invalid statistics month {value!r}; expected YYYY-MM")

    year = int(match.group("year"))
    month = int(match.group("month"))
    if month < 1 or month > 12:
        raise MonthCoverageError(f"Invalid statistics month {value!r}; expected YYYY-MM")
    return year, month


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def load_statement_dates(email_dir: Path) -> frozenset[date]:
    if not email_dir.exists():
        raise MonthCoverageError(f"Statement email directory does not exist: {email_dir}")
    if not email_dir.is_dir():
        raise MonthCoverageError(f"Statement email path is not a directory: {email_dir}")

    statement_dates: set[date] = set()
    for path in sorted(email_dir.glob("*.eml")):
        match = STATEMENT_FILENAME_PATTERN.fullmatch(path.name)
        if match is None:
            raise MonthCoverageError(
                f"Invalid statement email filename {path.name!r}; "
                "expected YYYY-MM-DD_<24 lowercase hex>.eml"
            )
        try:
            statement_dates.add(date.fromisoformat(match.group("mail_date")))
        except ValueError as exc:
            raise MonthCoverageError(
                f"Invalid statement email date in filename {path.name!r}"
            ) from exc
    return frozenset(statement_dates)


def build_month_coverage(
    months: tuple[str, ...],
    statement_dates: frozenset[date],
) -> tuple[MonthCoverage, ...]:
    seen: set[str] = set()
    coverage: list[MonthCoverage] = []

    for month_name in months:
        if month_name in seen:
            raise MonthCoverageError(f"Duplicate statistics month {month_name!r}")
        seen.add(month_name)

        year, month = _parse_month(month_name)
        next_year, next_month = _next_month(year, month)
        required_dates = (
            date(year, month, STATEMENT_DAY),
            date(next_year, next_month, STATEMENT_DAY),
        )
        is_complete = all(required_date in statement_dates for required_date in required_dates)
        coverage.append(
            MonthCoverage(
                month=month_name,
                is_complete=is_complete,
                show=is_complete,
            )
        )

    return tuple(coverage)


def load_month_coverage(
    months: tuple[str, ...],
    email_dir: Path,
) -> tuple[MonthCoverage, ...]:
    return build_month_coverage(months, load_statement_dates(email_dir))
