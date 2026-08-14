from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from family_spending.statistics_serialization import STATISTICS_SCHEMA_VERSION


class ProjectionQueryError(RuntimeError):
    """Raised when a generated projection cannot be served as coherent current state."""


def read_spending_statistics_projection(path: Path) -> dict[str, Any]:
    """Read the generated spending projection without triggering any financial recomputation."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectionQueryError(
            "Spending Statistics projection does not exist; run backend sync or application initialization"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionQueryError(
            f"Unable to read Spending Statistics projection from {path}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ProjectionQueryError(
            f"Spending Statistics projection {path} must contain a JSON object"
        )
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
