from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.manifest import (
    CURRENT_STORAGE_SCHEMA_VERSION,
    StorageManifestError,
    StorageMigrationRequiredError,
    UnsupportedStorageSchemaError,
    initialize_storage,
)
from family_spending.persistence.filesystem.unit_of_work import FileUnitOfWork, FileUnitOfWorkError


class StorageContractTests(unittest.TestCase):
    def test_layout_derives_canonical_paths_only_from_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = StorageLayout(Path(temp_dir).resolve())
            self.assertEqual(layout.manifest, layout.data_root / "manifest.json")
            self.assertEqual(layout.cmb_email_evidence, layout.data_root / "evidence" / "cmb-email")
            self.assertEqual(layout.manual_evidence, layout.data_root / "evidence" / "manual" / "records.jsonl")
            self.assertEqual(layout.source_links, layout.data_root / "state" / "identity" / "source-links.jsonl")
            self.assertEqual(layout.enrichment_decisions, layout.data_root / "state" / "enrichment" / "decisions.jsonl")
            self.assertEqual(layout.scheduled_rules, layout.data_root / "state" / "schedules" / "rules.json")
            self.assertEqual(layout.schedule_execution, layout.data_root / "state" / "schedules" / "execution.json")
            self.assertEqual(layout.derived_projections, layout.data_root / "derived" / "projections")

    def test_layout_rejects_relative_roots(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            StorageLayout(Path("data"))

    def test_fresh_storage_bootstrap_is_idempotent_and_writes_current_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_root = Path(temp_dir) / "household"
            layout = StorageLayout(data_root.resolve())
            now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
            first = initialize_storage(layout, now=now)
            second = initialize_storage(layout)
            payload = json.loads(layout.manifest.read_text(encoding="utf-8"))

            self.assertEqual(first, second)
            self.assertEqual(first.storage_schema_version, CURRENT_STORAGE_SCHEMA_VERSION)
            self.assertEqual(payload["created_at"], "2026-08-15T12:00:00Z")
            self.assertIsNone(payload["last_migrated_at"])
            for directory in layout.managed_directories:
                self.assertTrue(directory.is_dir())

    def test_failed_manifest_write_does_not_poison_fresh_root_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = StorageLayout((Path(temp_dir) / "household").resolve())
            with patch(
                "family_spending.persistence.filesystem.manifest.os.replace",
                side_effect=OSError("simulated manifest publish failure"),
            ):
                with self.assertRaisesRegex(OSError, "simulated manifest publish failure"):
                    initialize_storage(layout)

            self.assertFalse(layout.manifest.exists())
            self.assertEqual(tuple(layout.data_root.iterdir()), ())

            recovered = initialize_storage(layout)
            self.assertEqual(
                recovered.storage_schema_version,
                CURRENT_STORAGE_SCHEMA_VERSION,
            )
            self.assertTrue(layout.manifest.is_file())

    def test_older_or_newer_schema_never_enters_runtime_compatibility_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = StorageLayout(Path(temp_dir).resolve())
            layout.manifest.write_text(
                json.dumps(
                    {
                        "storage_schema_version": CURRENT_STORAGE_SCHEMA_VERSION + 1,
                        "created_at": "2026-08-15T12:00:00Z",
                        "last_migrated_at": None,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(UnsupportedStorageSchemaError):
                initialize_storage(layout)

        # Simulate a future program version to prove version 1 enters explicit migration,
        # rather than adding a permanent compatibility branch to the runtime.
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = StorageLayout(Path(temp_dir).resolve())
            layout.manifest.write_text(
                json.dumps(
                    {
                        "storage_schema_version": CURRENT_STORAGE_SCHEMA_VERSION,
                        "created_at": "2026-08-15T12:00:00Z",
                        "last_migrated_at": None,
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "family_spending.persistence.filesystem.manifest.CURRENT_STORAGE_SCHEMA_VERSION",
                CURRENT_STORAGE_SCHEMA_VERSION + 1,
            ):
                with self.assertRaises(StorageMigrationRequiredError):
                    initialize_storage(layout)

    def test_non_empty_unversioned_root_requires_explicit_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = StorageLayout(Path(temp_dir).resolve())
            (layout.data_root / "legacy.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(StorageManifestError, "non-empty"):
                initialize_storage(layout)

    def test_file_unit_of_work_commits_or_restores_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("before-first", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "boom"):
                with FileUnitOfWork((first, second), label="test mutation"):
                    first.write_text("after-first", encoding="utf-8")
                    second.write_text("created", encoding="utf-8")
                    raise RuntimeError("boom")
            self.assertEqual(first.read_text(encoding="utf-8"), "before-first")
            self.assertFalse(second.exists())

            with FileUnitOfWork((first, second), label="test mutation") as unit_of_work:
                first.write_text("committed", encoding="utf-8")
                second.write_text("created", encoding="utf-8")
                unit_of_work.commit()
            self.assertEqual(first.read_text(encoding="utf-8"), "committed")
            self.assertEqual(second.read_text(encoding="utf-8"), "created")

    def test_file_unit_of_work_requires_explicit_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.txt"
            path.write_text("before", encoding="utf-8")
            with self.assertRaisesRegex(FileUnitOfWorkError, "without commit"):
                with FileUnitOfWork((path,), label="test mutation"):
                    path.write_text("after", encoding="utf-8")
            self.assertEqual(path.read_text(encoding="utf-8"), "before")


if __name__ == "__main__":
    unittest.main()
