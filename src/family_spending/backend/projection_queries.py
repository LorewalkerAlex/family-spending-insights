from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from family_spending.financial_projection import FINANCIAL_SUMMARY_SCHEMA_VERSION
from family_spending.statistics_serialization import STATISTICS_SCHEMA_VERSION


class ProjectionQueryError(RuntimeError):
    """Raised when a generated projection cannot be served as coherent current state."""


def _read_projection_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectionQueryError(
            f"{label} projection does not exist; run backend sync"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionQueryError(
            f"Unable to read {label} projection from {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProjectionQueryError(
            f"{label} projection {path} must contain a JSON object"
        )
    return payload


def read_spending_statistics_projection(path: Path) -> dict[str, Any]:
    """Read the generated spending projection without triggering financial recomputation."""
    payload = _read_projection_object(path, "Spending Statistics")
    if payload.get("schema_version") != STATISTICS_SCHEMA_VERSION:
        raise ProjectionQueryError(
            "Spending Statistics projection has an unsupported schema version: "
            f"{payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("summary"), dict) or not isinstance(
        payload.get("months"), list
    ):
        raise ProjectionQueryError(
            f"Spending Statistics projection {path} is missing summary/months data"
        )
    return payload


def read_financial_summary_projection(path: Path) -> dict[str, Any]:
    """Read the generated financial summary without hiding a rebuild behind the query."""
    payload = _read_projection_object(path, "Financial Summary")
    if payload.get("schema_version") != FINANCIAL_SUMMARY_SCHEMA_VERSION:
        raise ProjectionQueryError(
            "Financial Summary projection has an unsupported schema version: "
            f"{payload.get('schema_version')!r}"
        )
    if not isinstance(payload.get("summary"), dict) or not isinstance(
        payload.get("months"), list
    ):
        raise ProjectionQueryError(
            f"Financial Summary projection {path} is missing summary/months data"
        )
    return payload
