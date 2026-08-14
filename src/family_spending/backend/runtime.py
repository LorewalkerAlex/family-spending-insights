from __future__ import annotations

from pathlib import Path

from family_spending.backend.paths import BackendPaths
from family_spending.backend.pipeline import (
    HouseholdPipeline,
    HouseholdSyncSummary,
    ProjectionRebuildSummary,
)
from family_spending.backend.state import CurrentHouseholdSnapshot


class BackendRuntimeNotReadyError(RuntimeError):
    """Raised when a caller requests current state before the runtime has been bootstrapped."""


def _path_fingerprint(path: Path) -> tuple[str, bool, int, int]:
    """Use cheap filesystem metadata to detect external state changes between queries."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (str(path), False, 0, 0)
    return (str(path), True, stat.st_size, stat.st_mtime_ns)


class BackendRuntime:
    """Own the current in-process backend snapshot and explicit pipeline lifecycle.

    It keeps query-facing state explicit and reusable while pipeline methods own synchronization
    and downstream rebuild lifecycles.
    """

    def __init__(
        self,
        paths: BackendPaths | None = None,
        *,
        pipeline: HouseholdPipeline | None = None,
    ) -> None:
        self.paths = paths or BackendPaths()
        self.pipeline = pipeline or HouseholdPipeline(self.paths)
        self._snapshot: CurrentHouseholdSnapshot | None = None
        self._fingerprint: tuple[tuple[str, bool, int, int], ...] | None = None

    def _state_fingerprint(self) -> tuple[tuple[str, bool, int, int], ...]:
        tracked = (
            self.paths.transactions,
            self.paths.manual_source,
            self.paths.source_links,
            self.paths.enrichment_state,
            self.paths.merchants,
            self.paths.categories,
        )
        return tuple(_path_fingerprint(path) for path in tracked)

    def sync_sources(self) -> HouseholdSyncSummary:
        """Run the full Source pipeline, then publish the resulting current snapshot."""
        summary = self.pipeline.sync_sources()
        self.refresh()
        return summary

    def bootstrap(self) -> HouseholdSyncSummary:
        """Establish a coherent current runtime from persisted Source state."""
        return self.sync_sources()

    def refresh(self) -> CurrentHouseholdSnapshot:
        """Reload current persisted state without rerunning Reconciliation."""
        snapshot = self.pipeline.load_current_state()
        self._snapshot = snapshot
        self._fingerprint = self._state_fingerprint()
        return snapshot

    def current_state(self) -> CurrentHouseholdSnapshot:
        """Reuse the cached snapshot unless an external persisted-state change is detected."""
        if self._snapshot is None or self._fingerprint is None:
            raise BackendRuntimeNotReadyError(
                "Backend runtime has not been bootstrapped; run sync_sources() first"
            )
        current_fingerprint = self._state_fingerprint()
        if current_fingerprint != self._fingerprint:
            return self.refresh()
        return self._snapshot

    def rebuild_projections(self) -> ProjectionRebuildSummary:
        """Refresh derived outputs from current state and retain the same runtime snapshot."""
        summary = self.pipeline.rebuild_projections()
        if self._snapshot is None:
            self.refresh()
        return summary
