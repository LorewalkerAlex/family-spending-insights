from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from threading import Event, Lock, Thread
from typing import Protocol

from family_spending.application.ports.source import SourceAcquirer, SourceAcquisitionResult
from family_spending.runtime.state import RuntimeStore


@dataclass(frozen=True)
class SourcePollResult:
    """One supervisor pass with source-local counts and whether downstream sync ran."""

    acquisitions: tuple[SourceAcquisitionResult, ...]
    source_sync_triggered: bool
    error: str | None = None


class SourceSupervisor:
    """Poll configured sources without allowing acquisition failures to terminate runtime service."""

    def __init__(
        self,
        acquirers: tuple[SourceAcquirer, ...],
        *,
        source_sync: Callable[[], object],
        runtime: RuntimeStore,
        interval_seconds: float,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("SourceSupervisor interval_seconds must be positive")
        self._acquirers = acquirers
        self._source_sync = source_sync
        self._runtime = runtime
        self._interval_seconds = interval_seconds
        self._pending_sync = bool(
            runtime.current_state().household.unreconciled_source_record_ids
        )
        self._stop = Event()
        self._thread: Thread | None = None
        self._lifecycle_lock = Lock()

    def poll_once(self) -> SourcePollResult:
        acquisitions: list[SourceAcquisitionResult] = []
        sync_triggered = False
        try:
            for acquirer in self._acquirers:
                result = acquirer.acquire()
                if result.source_type != acquirer.source_type:
                    raise RuntimeError(
                        f"Source acquirer {acquirer.source_type!r} returned result for "
                        f"{result.source_type!r}"
                    )
                acquisitions.append(result)
                if result.added_count > 0:
                    self._pending_sync = True

            if self._pending_sync:
                sync_triggered = True
                self._source_sync()
                self._pending_sync = bool(
                    self._runtime.current_state().household.unreconciled_source_record_ids
                )
            self._runtime.record_source_poll()
            return SourcePollResult(tuple(acquisitions), sync_triggered)
        except Exception as exc:
            self._runtime.record_source_poll(exc)
            return SourcePollResult(tuple(acquisitions), sync_triggered, str(exc))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._interval_seconds)

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("SourceSupervisor is already running")
            self._stop.clear()
            self._thread = Thread(
                target=self._run,
                name="family-spending-source-supervisor",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            self._stop.set()
        if thread is not None:
            thread.join(timeout=max(self._interval_seconds * 2, 1.0))
        with self._lifecycle_lock:
            self._thread = None


class ScheduledTick(Protocol):
    def __call__(self, today: date) -> object: ...


class SchedulerTrigger:
    """Invoke the Application scheduled-input use case from a runtime clock boundary."""

    def __init__(
        self,
        run_due: ScheduledTick,
        *,
        runtime: RuntimeStore,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._run_due = run_due
        self._runtime = runtime
        self._today = today

    def run_once(self) -> bool:
        try:
            self._run_due(self._today())
        except Exception as exc:
            self._runtime.record_scheduler_tick(exc)
            return False
        self._runtime.record_scheduler_tick()
        return True
