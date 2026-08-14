from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from family_spending.backend import (
    BackendPaths,
    BackendRuntime,
    BackendStateError,
    HouseholdPipeline,
)
from family_spending.cli import build_parser
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    read_transactions_csv,
    write_transactions_csv,
)
from family_spending.infrastructure.file_uow import FileUnitOfWork


MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
"""


def transaction(transaction_id: str, amount: str, *, source_index: int) -> CmbTransaction:
    """Build one deterministic CMB source record for backend lifecycle tests."""
    return CmbTransaction(
        transaction_id=transaction_id,
        transaction_date=date(2026, 1, source_index + 1),
        amount=Decimal(amount),
        description="支付宝-测试餐饮",
        source_email="statement.eml",
        source_index=source_index,
    )


class BackendArchitectureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.paths = BackendPaths(
            transactions=root / "transactions.csv",
            manual_source=root / "manual_source_records.jsonl",
            source_links=root / "transaction_source_links.jsonl",
            enrichment_state=root / "enrichment_state.jsonl",
            merchants=root / "merchants.yaml",
            categories=root / "categories.yaml",
            spending_statistics=root / "reports" / "spending_statistics.json",
            financial_summary=root / "reports" / "financial_summary.json",
            emails=root / "emails",
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.emails.mkdir()
        for index, statement_date in enumerate(("2026-01-10", "2026-02-10"), start=1):
            statement = self.paths.emails / f"{statement_date}_{format(index, '024x')}.eml"
            statement.write_bytes(b"test")
        write_transactions_csv(
            (transaction("cmb_one", "20", source_index=1),),
            self.paths.transactions,
        )

    def test_file_unit_of_work_commits_or_restores_all_participants(self) -> None:
        first = self.paths.transactions.parent / "first.txt"
        second = self.paths.transactions.parent / "second.txt"
        first.write_text("before-first", encoding="utf-8")
        second.write_text("before-second", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with FileUnitOfWork((first, second), label="test mutation") as unit_of_work:
                first.write_text("after-first", encoding="utf-8")
                second.write_text("after-second", encoding="utf-8")
                raise RuntimeError("boom")

        self.assertEqual(first.read_text(encoding="utf-8"), "before-first")
        self.assertEqual(second.read_text(encoding="utf-8"), "before-second")

        with FileUnitOfWork((first, second), label="test mutation") as unit_of_work:
            first.write_text("committed-first", encoding="utf-8")
            second.write_text("committed-second", encoding="utf-8")
            unit_of_work.commit()

        self.assertEqual(first.read_text(encoding="utf-8"), "committed-first")
        self.assertEqual(second.read_text(encoding="utf-8"), "committed-second")

    def test_runtime_reuses_current_snapshot_and_detects_external_source_changes(self) -> None:
        runtime = BackendRuntime(self.paths)
        summary = runtime.sync_sources()
        first_snapshot = runtime.current_state()
        second_snapshot = runtime.current_state()

        self.assertEqual(summary.raw_transactions, 1)
        self.assertIs(first_snapshot, second_snapshot)
        self.assertEqual(len(first_snapshot.transactions), 1)

        current = read_transactions_csv(self.paths.transactions)
        write_transactions_csv(
            current
            + (transaction("cmb_two", "30", source_index=2),),
            self.paths.transactions,
        )
        with self.assertRaisesRegex(BackendStateError, "sync"):
            runtime.current_state()

        refreshed = runtime.sync_sources()
        self.assertEqual(refreshed.raw_transactions, 2)
        self.assertEqual(len(runtime.current_state().transactions), 2)

    def test_projection_rebuild_uses_current_state_without_reconciliation(self) -> None:
        pipeline = HouseholdPipeline(self.paths)
        pipeline.sync_sources()
        links_before = self.paths.source_links.read_bytes()
        enrichment_before = self.paths.enrichment_state.read_bytes()

        self.paths.spending_statistics.write_text("stale", encoding="utf-8")
        self.paths.financial_summary.write_text("stale", encoding="utf-8")

        with (
            patch(
                "family_spending.reconciliation.CmbReconciler.reconcile",
                side_effect=AssertionError("CMB reconciliation must not run"),
            ),
            patch(
                "family_spending.reconciliation.ManualReconciler.reconcile",
                side_effect=AssertionError("Manual reconciliation must not run"),
            ),
        ):
            summary = pipeline.rebuild_projections()

        self.assertEqual(summary.transactions, 1)
        self.assertEqual(self.paths.source_links.read_bytes(), links_before)
        self.assertEqual(self.paths.enrichment_state.read_bytes(), enrichment_before)
        self.assertEqual(
            json.loads(
                self.paths.spending_statistics.read_text(encoding="utf-8")
            )["schema_version"],
            2,
        )
        self.assertEqual(
            json.loads(
                self.paths.financial_summary.read_text(encoding="utf-8")
            )["schema_version"],
            1,
        )

    def test_unified_cli_exposes_explicit_backend_lifecycle_commands(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["serve"]).command, "serve")
        self.assertEqual(parser.parse_args(["sync"]).command, "sync")
        jobs = parser.parse_args(["jobs", "run-due", "--as-of", "2026-08-14"])
        self.assertEqual(jobs.job, "run-due")
        self.assertEqual(jobs.as_of, date(2026, 8, 14))
        rebuild = parser.parse_args(["rebuild", "projections"])
        self.assertEqual(rebuild.rebuild_target, "projections")
        diagnose = parser.parse_args(["diagnose", "state"])
        self.assertEqual(diagnose.diagnose_target, "state")


if __name__ == "__main__":
    unittest.main()
