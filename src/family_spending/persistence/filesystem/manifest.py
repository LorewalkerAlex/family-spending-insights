from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone

from family_spending.persistence.filesystem.layout import StorageLayout

CURRENT_STORAGE_SCHEMA_VERSION = 1


class StorageManifestError(RuntimeError):
    """Base error for missing or invalid canonical storage metadata."""


class StorageMigrationRequiredError(StorageManifestError):
    """Raised when an older data root requires an explicit one-time migration."""


class UnsupportedStorageSchemaError(StorageManifestError):
    """Raised when this program is older than the household storage schema."""


@dataclass(frozen=True)
class StorageManifest:
    storage_schema_version: int
    created_at: datetime
    last_migrated_at: datetime | None = None


def _parse_timestamp(raw: object, field: str) -> datetime:
    if not isinstance(raw, str):
        raise StorageManifestError(f"manifest {field} must be an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StorageManifestError(f"manifest {field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise StorageManifestError(f"manifest {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise StorageManifestError("manifest timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_manifest(layout: StorageLayout, manifest: StorageManifest) -> None:
    payload = {
        "storage_schema_version": manifest.storage_schema_version,
        "created_at": _format_timestamp(manifest.created_at),
        "last_migrated_at": (
            _format_timestamp(manifest.last_migrated_at)
            if manifest.last_migrated_at is not None
            else None
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    layout.manifest.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{layout.manifest.name}.",
        suffix=".tmp",
        dir=layout.manifest.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, layout.manifest)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def read_manifest(layout: StorageLayout) -> StorageManifest:
    """Read and validate metadata before any canonical household state is trusted."""
    if not layout.manifest.is_file():
        raise StorageManifestError(f"Storage manifest is missing: {layout.manifest}")
    try:
        raw = json.loads(layout.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageManifestError(f"Unable to read storage manifest {layout.manifest}: {exc}") from exc
    if not isinstance(raw, dict):
        raise StorageManifestError("Storage manifest must contain one JSON object")

    version = raw.get("storage_schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise StorageManifestError("manifest storage_schema_version must be a positive integer")
    created_at = _parse_timestamp(raw.get("created_at"), "created_at")
    last_migrated_raw = raw.get("last_migrated_at")
    last_migrated_at = (
        None
        if last_migrated_raw is None
        else _parse_timestamp(last_migrated_raw, "last_migrated_at")
    )
    return StorageManifest(
        storage_schema_version=version,
        created_at=created_at,
        last_migrated_at=last_migrated_at,
    )


def require_current_schema(manifest: StorageManifest) -> None:
    """Fail closed so migration never becomes an implicit runtime compatibility branch."""
    if manifest.storage_schema_version < CURRENT_STORAGE_SCHEMA_VERSION:
        raise StorageMigrationRequiredError(
            "Storage schema is older than this program; run the explicit storage migration"
        )
    if manifest.storage_schema_version > CURRENT_STORAGE_SCHEMA_VERSION:
        raise UnsupportedStorageSchemaError(
            "Storage schema is newer than this program; refusing to start with older code"
        )


def initialize_storage(
    layout: StorageLayout,
    *,
    now: datetime | None = None,
) -> StorageManifest:
    """Initialize only a fresh root, or validate an already canonical root idempotently."""
    if layout.manifest.exists():
        manifest = read_manifest(layout)
        require_current_schema(manifest)
        for directory in layout.managed_directories:
            directory.mkdir(parents=True, exist_ok=True)
        return manifest

    if layout.data_root.exists() and any(layout.data_root.iterdir()):
        raise StorageManifestError(
            "Refusing to initialize a non-empty data root without manifest.json; migrate it explicitly"
        )

    created_at = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        raise StorageManifestError("Storage initialization timestamp must be timezone-aware")
    manifest = StorageManifest(
        storage_schema_version=CURRENT_STORAGE_SCHEMA_VERSION,
        created_at=created_at.astimezone(timezone.utc),
    )
    # Publish schema identity before optional directory scaffolding. If directory
    # creation later fails, the next bootstrap can safely validate the manifest
    # and retry missing directories instead of misclassifying our own partial
    # initialization as unversioned household data.
    _atomic_write_manifest(layout, manifest)
    for directory in layout.managed_directories:
        directory.mkdir(parents=True, exist_ok=True)
    return manifest
