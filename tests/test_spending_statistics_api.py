from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from family_spending.application import ApplicationPaths
from family_spending.backend.application import RuntimeFamilySpendingApplication
from family_spending.backend.http_server import create_runtime_http_server
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    write_transactions_csv,
)

MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
"""


class SpendingStatisticsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.paths = ApplicationPaths(
            transactions=root / "transactions.csv",
            manual_source=root / "manual_source_records.jsonl",
            source_links=root / "transaction_source_links.jsonl",
            enrichment_state=root / "enrichment_state.jsonl",
            merchants=root / "merchants.yaml",
            categories=root / "categories.yaml",
            spending_statistics=root / "reports" / "spending_statistics.json",
            emails=root / "emails",
        )
        self.paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        self.paths.categories.write_text(CATEGORIES, encoding="utf-8")
        self.paths.emails.mkdir()
        for index, statement_date in enumerate(
            ("2025-12-10", "2026-01-10", "2026-02-10"),
            start=1,
        ):
            digest = format(index, "024x")
            (self.paths.emails / f"{statement_date}_{digest}.eml").write_bytes(b"test")
        write_transactions_csv(
            (
                CmbTransaction(
                    transaction_id="cmb_food",
                    transaction_date=date(2026, 1, 2),
                    amount=Decimal("20"),
                    description="支付宝-测试餐饮",
                    source_email="statement.eml",
                    source_index=1,
                ),
            ),
            self.paths.transactions,
        )
        self.application = RuntimeFamilySpendingApplication(self.paths)
        self.application.initialize()
        self.server = create_runtime_http_server(self.application, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def _json_request(self, path: str) -> tuple[int, dict[str, object]]:
        request = Request(f"{self.base_url}{path}", method="GET")
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_runtime_spending_statistics_query_returns_projection_without_source_sync(self) -> None:
        expected = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))
        with patch(
            "family_spending.backend.pipeline.HouseholdPipeline.sync_sources",
            side_effect=AssertionError("GET must not run Source Sync"),
        ):
            status, body = self._json_request("/api/spending-statistics")

        self.assertEqual(status, 200)
        self.assertEqual(body, {"spending_statistics": expected})

    def test_runtime_application_owns_the_projection_query_boundary(self) -> None:
        expected = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))
        self.assertEqual(self.application.get_spending_statistics(), expected)

    def test_runtime_server_preserves_existing_routes(self) -> None:
        status, body = self._json_request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_missing_spending_projection_is_reported_as_current_state_conflict(self) -> None:
        self.paths.spending_statistics.unlink()
        status, body = self._json_request("/api/spending-statistics")
        self.assertEqual(status, 409)
        self.assertIn("does not exist", body["error"])


if __name__ == "__main__":
    unittest.main()
