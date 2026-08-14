from __future__ import annotations

from dataclasses import dataclass

from family_spending.backend.pipeline import (
    HouseholdSourceSyncPlan,
    ManualSourceReplacement,
)
from family_spending.backend.runtime import BackendRuntime
from family_spending.infrastructure.file_uow import (
    FileUnitOfWork,
    FileUnitOfWorkRollbackError,
)
from family_spending.manual_source import (
    ManualSourceDataError,
    ManualSourceEntry,
    write_manual_source_entries,
)


class ManualInputCommandRollbackError(RuntimeError):
    """Raised when a runtime-owned Manual Input command cannot restore its file boundary."""


@dataclass(frozen=True)
class ManualInputCommandResult:
    source_record_id: str
    transaction_id: str
    action: str


@dataclass(frozen=True)
class ManualInputDeleteResult:
    source_record_id: str
    transaction_id: str
    transaction_removed: bool


class ManualInputCommandService:
    """Own Manual Source mutations on top of BackendRuntime and HouseholdPipeline."""

    def __init__(self, runtime: BackendRuntime) -> None:
        self.runtime = runtime

    def create(self, entry: ManualSourceEntry) -> ManualInputCommandResult:
        """Add one source-native Manual record and commit one coordinated Source sync."""
        snapshot = self.runtime.current_state()
        if any(item.id == entry.id for item in snapshot.manual_entries):
            raise ManualSourceDataError(
                f"Manual source record {entry.id!r} already exists"
            )
        manual_entries = snapshot.manual_entries + (entry,)
        plan = self.runtime.pipeline.plan_source_sync(
            manual_entries=manual_entries,
            submitted_source_ids=(entry.id,),
        )
        decision = self._decision(plan, entry.id)
        self._commit(
            manual_entries,
            plan,
            label="Manual Input create",
        )
        return ManualInputCommandResult(
            source_record_id=entry.id,
            transaction_id=decision.transaction_id,
            action=decision.action,
        )

    def correct(
        self,
        source_record_id: str,
        replacement: ManualSourceEntry,
        *,
        update_note: bool,
    ) -> ManualInputCommandResult:
        """Replace one Manual Source identity while preserving established Transaction semantics."""
        if replacement.id == source_record_id:
            raise ManualSourceDataError(
                "Manual input correction must create a new source record id"
            )
        snapshot = self.runtime.current_state()
        replacement_index = next(
            (
                index
                for index, item in enumerate(snapshot.manual_entries)
                if item.id == source_record_id
            ),
            None,
        )
        if replacement_index is None:
            raise ManualSourceDataError(
                f"Manual source record {source_record_id!r} does not exist"
            )
        if any(item.id == replacement.id for item in snapshot.manual_entries):
            raise ManualSourceDataError(
                f"Manual source record {replacement.id!r} already exists"
            )
        previous_link = next(
            (
                item
                for item in snapshot.source_links
                if item.source_record_id == source_record_id
            ),
            None,
        )
        if previous_link is None:
            raise ManualSourceDataError(
                f"Manual source record {source_record_id!r} has no current Transaction link"
            )

        manual_entries = list(snapshot.manual_entries)
        previous_entry = manual_entries[replacement_index]
        manual_entries[replacement_index] = replacement
        candidate_entries = tuple(manual_entries)
        plan = self.runtime.pipeline.plan_source_sync(
            manual_entries=candidate_entries,
            manual_replacement=ManualSourceReplacement(
                previous_entry=previous_entry,
                replacement_entry=replacement,
                previous_link=previous_link,
                update_note=update_note,
            ),
        )
        decision = self._decision(plan, replacement.id)
        self._commit(
            candidate_entries,
            plan,
            label="Manual Input correction",
        )
        return ManualInputCommandResult(
            source_record_id=replacement.id,
            transaction_id=decision.transaction_id,
            action=decision.action,
        )

    def delete(self, source_record_id: str) -> ManualInputDeleteResult:
        """Remove one Manual Source and let Source authority determine Transaction survival."""
        snapshot = self.runtime.current_state()
        if not any(item.id == source_record_id for item in snapshot.manual_entries):
            raise ManualSourceDataError(
                f"Manual source record {source_record_id!r} does not exist"
            )
        previous_link = next(
            (
                item
                for item in snapshot.source_links
                if item.source_record_id == source_record_id
            ),
            None,
        )
        if previous_link is None:
            raise ManualSourceDataError(
                f"Manual source record {source_record_id!r} has no current Transaction link"
            )

        manual_entries = tuple(
            item for item in snapshot.manual_entries if item.id != source_record_id
        )
        plan = self.runtime.pipeline.plan_source_sync(
            manual_entries=manual_entries,
        )
        transaction_removed = previous_link.transaction_id not in {
            link.transaction_id for link in plan.source_links
        }
        self._commit(
            manual_entries,
            plan,
            label="Manual Input deletion",
        )
        return ManualInputDeleteResult(
            source_record_id=source_record_id,
            transaction_id=previous_link.transaction_id,
            transaction_removed=transaction_removed,
        )

    def _commit(
        self,
        manual_entries: tuple[ManualSourceEntry, ...],
        plan: HouseholdSourceSyncPlan,
        *,
        label: str,
    ) -> None:
        """Commit Manual Source plus all downstream Source-sync files as one local unit."""
        persisted_paths = (
            self.runtime.paths.manual_source,
            *self.runtime.pipeline.source_sync_persisted_paths(),
        )
        try:
            with FileUnitOfWork(
                persisted_paths,
                label=label,
            ) as unit_of_work:
                write_manual_source_entries(
                    manual_entries,
                    self.runtime.paths.manual_source,
                )
                self.runtime.pipeline.write_source_sync_plan(plan)
                self.runtime.refresh()
                unit_of_work.commit()
        except FileUnitOfWorkRollbackError as exc:
            raise ManualInputCommandRollbackError(str(exc)) from exc

    @staticmethod
    def _decision(plan: HouseholdSourceSyncPlan, source_record_id: str):
        decision = next(
            (
                item
                for item in plan.decisions
                if item.source_record_id == source_record_id
            ),
            None,
        )
        if decision is None:
            raise ManualSourceDataError(
                "Manual Source was not present in the Source sync decision set: "
                f"{source_record_id!r}"
            )
        return decision
