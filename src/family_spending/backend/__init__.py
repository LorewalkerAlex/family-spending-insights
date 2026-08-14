"""Backend runtime, pipeline, and current-state boundaries for Family Spending."""

from family_spending.backend.paths import BackendPaths
from family_spending.backend.pipeline import (
    HouseholdPipeline,
    HouseholdPipelineRollbackError,
    HouseholdSyncSummary,
    ProjectionRebuildSummary,
)
from family_spending.backend.runtime import BackendRuntime, BackendRuntimeNotReadyError
from family_spending.backend.state import (
    BackendStateError,
    CurrentHouseholdSnapshot,
    load_current_household_snapshot,
)

__all__ = [
    "BackendPaths",
    "BackendRuntime",
    "BackendRuntimeNotReadyError",
    "BackendStateError",
    "CurrentHouseholdSnapshot",
    "HouseholdPipeline",
    "HouseholdPipelineRollbackError",
    "HouseholdSyncSummary",
    "ProjectionRebuildSummary",
    "load_current_household_snapshot",
]
