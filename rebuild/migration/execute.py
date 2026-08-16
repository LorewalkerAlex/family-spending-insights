from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from family_spending.config import AppConfig, CmbEmailSourceConfig, SourceConfig, StorageConfig
from family_spending.persistence.filesystem.cmb_email_evidence_store import CmbEmailEvidenceStore
from family_spending.persistence.filesystem.enrichment_store import FilesystemEnrichmentDecisionStore
from family_spending.persistence.filesystem.feedback_store import FilesystemFeedbackStore
from family_spending.persistence.filesystem.identity_store import FilesystemIdentityStore
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.manifest import initialize_storage
from family_spending.persistence.filesystem.manual_evidence_store import ManualEvidenceStore
from family_spending.persistence.filesystem.mapping_store import FilesystemMappingStore
from family_spending.persistence.filesystem.schedule_store import FilesystemScheduleStore
from family_spending.runtime.composition import compose_runtime
from rebuild.migration.plan import MigrationAudit, MigrationPlan, MigrationPlanError
from rebuild.migration.semantic import (
    compare_semantic_manifests,
    semantic_manifest_from_plan,
    semantic_manifest_from_runtime,
)


class MigrationExecutionError(RuntimeError):
    """Raised when a validated migration plan cannot be published atomically."""


@dataclass(frozen=True)
class MigrationExecutionResult:
    """Describe one successful atomic canonical sandbox publication."""

    target_root: Path
    audit_output: Path | None
    transaction_count: int
    source_link_count: int
    reused_source_record_count: int


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _config(root: Path) -> AppConfig:
    return AppConfig(
        storage=StorageConfig(data_root=root),
        sources=SourceConfig(cmb_email=CmbEmailSourceConfig(enabled=False)),
    )


def _materialize(plan: MigrationPlan, root: Path) -> None:
    layout = StorageLayout(root)
    initialize_storage(layout)
    cmb = CmbEmailEvidenceStore(layout)
    for evidence in plan.cmb_evidence:
        if not cmb.add(evidence):
            raise MigrationExecutionError("Fresh migration staging unexpectedly contained CMB evidence")
    ManualEvidenceStore(layout).replace_all(plan.manual_evidence)
    FilesystemIdentityStore(layout).replace(plan.source_links)
    FilesystemMappingStore(layout).replace(plan.mappings)
    FilesystemEnrichmentDecisionStore(layout).replace(plan.enrichment_decisions)
    schedule = FilesystemScheduleStore(layout)
    schedule.replace_rules(plan.scheduled_rules)
    schedule.replace_execution(plan.schedule_execution)
    FilesystemFeedbackStore(layout).replace(plan.feedback)


def _validate_materialized(plan: MigrationPlan, root: Path) -> int:
    components = compose_runtime(_config(root))
    state = components.runtime.current_state()
    if state.household.unreconciled_source_record_ids:
        raise MigrationExecutionError(
            "Materialized migration has unreconciled SourceRecords before Source Sync"
        )
    if tuple(item.id for item in state.household.transactions) != tuple(
        item.id for item in plan.transactions
    ):
        raise MigrationExecutionError("Materialized Transaction identity/order differs from plan")
    compare_semantic_manifests(
        semantic_manifest_from_plan(plan),
        semantic_manifest_from_runtime(components),
    )

    result = components.application.sync_sources()
    expected_sources = len(plan.source_records)
    if (
        result.source_record_count != expected_sources
        or result.transaction_count != len(plan.transactions)
        or result.created_count != 0
        or result.matched_count != 0
        or result.reused_count != expected_sources
    ):
        raise MigrationExecutionError(
            "First canonical Source Sync after migration did not reuse every migrated identity"
        )
    compare_semantic_manifests(
        semantic_manifest_from_plan(plan),
        semantic_manifest_from_runtime(components),
    )

    restarted = compose_runtime(_config(root))
    compare_semantic_manifests(
        semantic_manifest_from_plan(plan),
        semantic_manifest_from_runtime(restarted),
    )
    return result.reused_count


def execute_migration(
    plan: MigrationPlan,
    target_root: Path | str,
    *,
    audit_output: Path | str | None = None,
) -> MigrationExecutionResult:
    """Materialize a validated plan in sibling staging, validate it, then publish the target once."""
    target = Path(target_root).expanduser().resolve()
    audit = None if audit_output is None else Path(audit_output).expanduser().resolve()
    if target.exists():
        raise MigrationExecutionError(f"Migration target already exists: {target}")
    if audit is not None and audit.exists():
        raise MigrationExecutionError(f"Migration audit output already exists: {audit}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.migration-{uuid.uuid4().hex}"
    if staging.exists():
        raise MigrationExecutionError(f"Migration staging path unexpectedly exists: {staging}")
    published = False
    audit_temp: Path | None = None
    try:
        _materialize(plan, staging)
        reused = _validate_materialized(plan, staging)
        if audit is not None:
            audit.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{audit.name}.",
                suffix=".tmp",
                dir=audit.parent,
            )
            audit_temp = Path(temp_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(plan.audit.to_dict(), handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                audit_temp.unlink(missing_ok=True)
                audit_temp = None
                raise
        os.replace(staging, target)
        published = True
        if audit is not None and audit_temp is not None:
            try:
                os.replace(audit_temp, audit)
                audit_temp = None
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                published = False
                raise
        return MigrationExecutionResult(
            target_root=target,
            audit_output=audit,
            transaction_count=len(plan.transactions),
            source_link_count=len(plan.source_links),
            reused_source_record_count=reused,
        )
    except MigrationExecutionError:
        raise
    except Exception as exc:
        raise MigrationExecutionError(f"Migration materialization failed: {exc}") from exc
    finally:
        if not published:
            shutil.rmtree(staging, ignore_errors=True)
        if audit_temp is not None:
            audit_temp.unlink(missing_ok=True)
