from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from family_spending.domain.transaction import SourceLink
from family_spending.persistence.filesystem.identity_store import (
    FilesystemIdentityStore,
    IdentityStoreError,
)
from family_spending.persistence.filesystem.layout import StorageLayout


class IdentityStoreContractTests(unittest.TestCase):
    def store(self, root: Path) -> FilesystemIdentityStore:
        return FilesystemIdentityStore(StorageLayout(root.resolve()))

    def test_missing_store_is_empty_and_round_trip_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(Path(temp_dir))
            self.assertEqual(store.load(), ())
            links = (
                SourceLink("txn_a", "src_a", "authoritative"),
                SourceLink("txn_a", "src_b", "supporting"),
                SourceLink("txn_b", "src_c", "authoritative"),
            )
            store.replace(links)
            self.assertEqual(store.load(), links)

    def test_replace_rejects_duplicate_source_or_multiple_authority(self) -> None:
        invalid_sets = (
            (
                SourceLink("txn_a", "src_a", "authoritative"),
                SourceLink("txn_b", "src_a", "authoritative"),
            ),
            (
                SourceLink("txn_a", "src_a", "authoritative"),
                SourceLink("txn_a", "src_b", "authoritative"),
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(Path(temp_dir))
            for links in invalid_sets:
                with self.subTest(links=links):
                    with self.assertRaises(IdentityStoreError):
                        store.replace(links)

    def test_load_rejects_unknown_fields_and_supporting_only_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(Path(temp_dir))
            store.path.parent.mkdir(parents=True)
            store.path.write_text(
                json.dumps(
                    {
                        "transaction_id": "txn_a",
                        "source_record_id": "src_a",
                        "role": "authoritative",
                        "legacy": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(IdentityStoreError):
                store.load()

            store.path.write_text(
                json.dumps(
                    {
                        "transaction_id": "txn_a",
                        "source_record_id": "src_a",
                        "role": "supporting",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IdentityStoreError, "no authoritative"):
                store.load()

    def test_empty_replace_removes_identity_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(Path(temp_dir))
            store.replace((SourceLink("txn_a", "src_a", "authoritative"),))
            self.assertTrue(store.path.exists())
            store.replace(())
            self.assertFalse(store.path.exists())


if __name__ == "__main__":
    unittest.main()
