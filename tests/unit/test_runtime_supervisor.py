from __future__ import annotations

import unittest
from datetime import date
from types import MappingProxyType

from family_spending.application.ports.source import SourceAcquisitionResult
from family_spending.domain.mapping import MappingCatalog
from family_spending.runtime.state import HouseholdSnapshot, QueryIndexes, RuntimeCandidate, RuntimeStore
from family_spending.runtime.supervisor import SchedulerTrigger, SourceSupervisor


def _runtime() -> RuntimeStore:
    household = HouseholdSnapshot(
        source_records=(),
        unreconciled_source_record_ids=(),
        source_links=(),
        transactions=(),
        mappings=MappingCatalog.empty(),
        enrichment_decisions=(),
        enrichments=(),
        statement_dates=frozenset(),
        net_consumption=(),
        spending_payload=MappingProxyType({}),
        financial_payload=MappingProxyType({}),
    )
    empty = MappingProxyType({})
    runtime = RuntimeStore()
    runtime.bootstrap(RuntimeCandidate(household, QueryIndexes(empty, empty, empty, empty, empty, empty, empty, ())))
    return runtime


class _Acquirer:
    source_type = "fake"

    def __init__(self, added_counts: list[int]) -> None:
        self.added_counts = added_counts
        self.calls = 0

    def acquire(self) -> SourceAcquisitionResult:
        index = min(self.calls, len(self.added_counts) - 1)
        added = self.added_counts[index]
        self.calls += 1
        return SourceAcquisitionResult("fake", 1 if added else 0, added)


class _WrongResultAcquirer:
    source_type = "fake"

    def acquire(self) -> SourceAcquisitionResult:
        return SourceAcquisitionResult("other", 0, 0)


class _FailingAcquirer:
    source_type = "fake"

    def acquire(self):
        raise OSError("mailbox unavailable")


class RuntimeSupervisorTests(unittest.TestCase):
    def test_new_evidence_triggers_source_sync_once(self) -> None:
        runtime = _runtime()
        sync_calls: list[str] = []
        supervisor = SourceSupervisor(
            (_Acquirer([1]),),
            source_sync=lambda: sync_calls.append("sync"),
            runtime=runtime,
            interval_seconds=60,
        )
        result = supervisor.poll_once()
        self.assertEqual(sync_calls, ["sync"])
        self.assertTrue(result.source_sync_triggered)
        self.assertIsNone(result.error)
        self.assertIsNone(runtime.current_state().operational.last_source_poll_error)

    def test_failed_sync_is_retried_even_when_next_poll_adds_no_new_evidence(self) -> None:
        runtime = _runtime()
        calls = 0

        def sync() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("sync failed")

        supervisor = SourceSupervisor(
            (_Acquirer([1, 0]),),
            source_sync=sync,
            runtime=runtime,
            interval_seconds=60,
        )
        first = supervisor.poll_once()
        second = supervisor.poll_once()
        self.assertTrue(first.source_sync_triggered)
        self.assertEqual(first.error, "sync failed")
        self.assertTrue(second.source_sync_triggered)
        self.assertIsNone(second.error)
        self.assertEqual(calls, 2)


    def test_unreconciled_persistent_evidence_triggers_recovery_sync_without_new_acquisition(self) -> None:
        runtime = _runtime()
        current = runtime.current_state()
        pending_household = type(current.household)(
            source_records=current.household.source_records,
            unreconciled_source_record_ids=("src_pending",),
            source_links=current.household.source_links,
            transactions=current.household.transactions,
            mappings=current.household.mappings,
            enrichment_decisions=current.household.enrichment_decisions,
            enrichments=current.household.enrichments,
            statement_dates=current.household.statement_dates,
            net_consumption=current.household.net_consumption,
            spending_payload=current.household.spending_payload,
            financial_payload=current.household.financial_payload,
        )
        runtime.publish(
            RuntimeCandidate(pending_household, current.indexes),
            mutation_label="prepare pending evidence",
        )
        calls: list[str] = []
        supervisor = SourceSupervisor(
            (_Acquirer([0]),),
            source_sync=lambda: calls.append("sync"),
            runtime=runtime,
            interval_seconds=60,
        )
        result = supervisor.poll_once()
        self.assertTrue(result.source_sync_triggered)
        self.assertEqual(calls, ["sync"])


    def test_acquirer_result_source_type_mismatch_is_recorded_as_poll_failure(self) -> None:
        runtime = _runtime()
        supervisor = SourceSupervisor(
            (_WrongResultAcquirer(),),
            source_sync=lambda: None,
            runtime=runtime,
            interval_seconds=60,
        )
        result = supervisor.poll_once()
        self.assertIn("returned result", result.error or "")
        self.assertIn("returned result", runtime.current_state().operational.last_source_poll_error or "")

    def test_acquisition_failure_is_recorded_without_terminating_runtime(self) -> None:
        runtime = _runtime()
        supervisor = SourceSupervisor(
            (_FailingAcquirer(),),
            source_sync=lambda: None,
            runtime=runtime,
            interval_seconds=60,
        )
        result = supervisor.poll_once()
        self.assertEqual(result.error, "mailbox unavailable")
        self.assertEqual(runtime.current_state().generation, 1)
        self.assertEqual(
            runtime.current_state().operational.last_source_poll_error,
            "mailbox unavailable",
        )

    def test_scheduler_trigger_records_failure_and_success_without_mutating_household(self) -> None:
        runtime = _runtime()
        household = runtime.current_state().household
        trigger = SchedulerTrigger(
            lambda today: (_ for _ in ()).throw(RuntimeError(f"failed {today}")),
            runtime=runtime,
            today=lambda: date(2026, 8, 16),
        )
        self.assertFalse(trigger.run_once())
        self.assertIn("failed 2026-08-16", runtime.current_state().operational.last_scheduler_error or "")
        self.assertIs(runtime.current_state().household, household)

        success = SchedulerTrigger(
            lambda today: None,
            runtime=runtime,
            today=lambda: date(2026, 8, 16),
        )
        self.assertTrue(success.run_once())
        self.assertIsNone(runtime.current_state().operational.last_scheduler_error)


if __name__ == "__main__":
    unittest.main()
