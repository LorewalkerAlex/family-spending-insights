from __future__ import annotations

import threading
import unittest
from types import MappingProxyType

from family_spending.domain.mapping import MappingCatalog
from family_spending.runtime.coordinator import MutationCoordinator
from family_spending.runtime.state import (
    HouseholdSnapshot,
    QueryIndexes,
    RuntimeCandidate,
    RuntimeStore,
)


def _candidate(value: int) -> RuntimeCandidate:
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
        spending_payload=MappingProxyType({"value": value}),
        financial_payload=MappingProxyType({}),
    )
    empty = MappingProxyType({})
    indexes = QueryIndexes(empty, empty, empty, empty, empty, empty, empty, ())
    return RuntimeCandidate(household, indexes)


class _Builder:
    def __init__(self, state: dict[str, int]) -> None:
        self.state = state
        self.fail = False

    def build(self) -> RuntimeCandidate:
        if self.fail:
            raise RuntimeError("build failed")
        return _candidate(self.state["value"])


class _UnitOfWork:
    def __init__(self, state: dict[str, int]) -> None:
        self.state = state
        self.before = 0
        self.committed = False

    def __enter__(self):
        self.before = self.state["value"]
        return self

    def commit(self) -> None:
        self.committed = True

    def __exit__(self, exc_type, exc, traceback):
        if exc is not None or not self.committed:
            self.state["value"] = self.before
        return False


class MutationCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.persisted = {"value": 1}
        self.builder = _Builder(self.persisted)
        self.runtime = RuntimeStore()
        self.runtime.bootstrap(self.builder.build())
        self.coordinator = MutationCoordinator(self.runtime, self.builder)

    def test_success_commits_then_publishes_exactly_one_generation(self) -> None:
        result = self.coordinator.execute(
            label="set value",
            unit_of_work=_UnitOfWork(self.persisted),
            mutation=lambda: self.persisted.__setitem__("value", 2) or "ok",
        )
        self.assertEqual(result, "ok")
        self.assertEqual(self.persisted["value"], 2)
        current = self.runtime.current_state()
        self.assertEqual(current.generation, 2)
        self.assertEqual(current.household.spending_payload["value"], 2)
        self.assertIsNone(current.operational.current_mutation)

    def test_mutation_failure_rolls_back_and_keeps_old_household_generation(self) -> None:
        def fail() -> None:
            self.persisted["value"] = 9
            raise RuntimeError("mutation failed")

        with self.assertRaisesRegex(RuntimeError, "mutation failed"):
            self.coordinator.execute(
                label="failing value",
                unit_of_work=_UnitOfWork(self.persisted),
                mutation=fail,
            )
        self.assertEqual(self.persisted["value"], 1)
        current = self.runtime.current_state()
        self.assertEqual(current.generation, 1)
        self.assertEqual(current.household.spending_payload["value"], 1)
        self.assertEqual(current.operational.last_mutation_error, "mutation failed")

    def test_candidate_rebuild_failure_rolls_back_before_publication(self) -> None:
        self.builder.fail = True
        with self.assertRaisesRegex(RuntimeError, "build failed"):
            self.coordinator.execute(
                label="bad rebuild",
                unit_of_work=_UnitOfWork(self.persisted),
                mutation=lambda: self.persisted.__setitem__("value", 5),
            )
        self.assertEqual(self.persisted["value"], 1)
        self.assertEqual(self.runtime.current_state().generation, 1)

    def test_concurrent_reader_sees_complete_old_household_until_commit(self) -> None:
        started = threading.Event()
        release = threading.Event()
        old_household = self.runtime.current_state().household

        def mutation() -> None:
            self.persisted["value"] = 2
            started.set()
            self.assertTrue(release.wait(timeout=2))

        worker = threading.Thread(
            target=lambda: self.coordinator.execute(
                label="blocked mutation",
                unit_of_work=_UnitOfWork(self.persisted),
                mutation=mutation,
            )
        )
        worker.start()
        self.assertTrue(started.wait(timeout=2))
        during = self.runtime.current_state()
        self.assertIs(during.household, old_household)
        self.assertEqual(during.household.spending_payload["value"], 1)
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        after = self.runtime.current_state()
        self.assertEqual(after.generation, 2)
        self.assertEqual(after.household.spending_payload["value"], 2)


if __name__ == "__main__":
    unittest.main()
