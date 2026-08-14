from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


class FileUnitOfWorkError(RuntimeError):
    """Base error for file-backed commit-boundary failures."""


class FileUnitOfWorkRollbackError(FileUnitOfWorkError):
    """Raised when a failed mutation cannot fully restore its captured files."""


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    existed: bool
    contents: bytes | None


def _snapshot_file(path: Path) -> _FileSnapshot:
    """Capture exact pre-mutation bytes without requiring the file to exist."""
    if not path.exists():
        return _FileSnapshot(path=path, existed=False, contents=None)
    return _FileSnapshot(path=path, existed=True, contents=path.read_bytes())


def _restore_file(snapshot: _FileSnapshot) -> None:
    """Restore one file atomically, or remove a file created after the snapshot."""
    path = snapshot.path
    if not snapshot.existed:
        path.unlink(missing_ok=True)
        return

    assert snapshot.contents is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".rollback",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(snapshot.contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


class FileUnitOfWork:
    """Provide one reusable rollback boundary for coordinated local file mutations.

    Writes still happen through the owning repositories/stores. The unit of work owns only
    the cross-file commit boundary, so domain and application code no longer need to repeat
    byte snapshots and rollback mechanics for every feature.
    """

    def __init__(self, paths: Iterable[Path], *, label: str) -> None:
        ordered_paths: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            normalized = Path(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            ordered_paths.append(normalized)
        if not ordered_paths:
            raise ValueError("FileUnitOfWork requires at least one path")
        self._paths = tuple(ordered_paths)
        self._label = label
        self._snapshots: tuple[_FileSnapshot, ...] | None = None
        self._committed = False

    def __enter__(self) -> FileUnitOfWork:
        if self._snapshots is not None:
            raise FileUnitOfWorkError(f"{self._label} unit of work cannot be entered twice")
        self._snapshots = tuple(_snapshot_file(path) for path in self._paths)
        return self

    def commit(self) -> None:
        """Mark the coordinated writes as successful before leaving the context."""
        if self._snapshots is None:
            raise FileUnitOfWorkError(f"{self._label} unit of work has not been entered")
        self._committed = True

    def _rollback(self, original_error: BaseException) -> None:
        assert self._snapshots is not None
        failures: list[str] = []
        for snapshot in reversed(self._snapshots):
            try:
                _restore_file(snapshot)
            except Exception as exc:  # pragma: no cover - requires secondary storage failure
                failures.append(f"{snapshot.path}: {exc}")
        if failures:
            raise FileUnitOfWorkRollbackError(
                f"{self._label} failed and rollback could not fully restore persisted state: "
                + "; ".join(failures)
            ) from original_error

    def __exit__(self, exc_type, exc, traceback) -> bool:
        assert self._snapshots is not None
        if exc is not None:
            self._rollback(exc)
            return False
        if self._committed:
            return False

        error = FileUnitOfWorkError(
            f"{self._label} left its file unit of work without commit()"
        )
        self._rollback(error)
        raise error
