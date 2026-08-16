from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Protocol, TypeVar

from family_spending.application.ports.storage import UnitOfWork
from family_spending.runtime.state import RuntimeCandidate, RuntimeStore

ResultT = TypeVar("ResultT")


class RuntimeCandidateBuilder(Protocol):
    def build(self) -> RuntimeCandidate: ...


class MutationCoordinator:
    """Serialize authoritative mutations and publish exactly one rebuilt snapshot on success."""

    def __init__(
        self,
        runtime: RuntimeStore,
        builder: RuntimeCandidateBuilder,
    ) -> None:
        self._runtime = runtime
        self._builder = builder
        self._writer_lock = Lock()

    def execute(
        self,
        *,
        label: str,
        unit_of_work: UnitOfWork,
        mutation: Callable[[], ResultT],
    ) -> ResultT:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Mutation label must be non-empty")

        self._runtime.adjust_queued_mutations(1)
        with self._writer_lock:
            self._runtime.adjust_queued_mutations(-1)
            self._runtime.begin_mutation(label)
            try:
                with unit_of_work:
                    result = mutation()
                    candidate = self._builder.build()
                    unit_of_work.commit()
                self._runtime.publish(candidate, mutation_label=label)
                return result
            except BaseException as exc:
                self._runtime.record_mutation_failure(exc)
                raise
