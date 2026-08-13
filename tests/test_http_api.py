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

from family_spending.application import ApplicationPaths, FamilySpendingApplication
from family_spending.http_api import create_http_server
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    write_transactions_csv,
)

MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
测试家电:
  - 支付宝-测试家电
"""
CATEGORIES = """\
餐饮美食:
  - 测试餐饮
家居家电:
  - 测试家电
"""


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        paths = ApplicationPaths(
            transactions=root / "transactions.csv",
            manual_source=root / "manual_source_records.jsonl",
            source_links=root / "transaction_source_links.jsonl",
            enrichment_state=root / "enrichment_state.jsonl",
            merchants=root / "merchants.yaml",
            categories=root / "categories.yaml",
            spending_statistics=root / "reports" / "spending_statistics.json",
            emails=root / "emails",
        )
        self.paths = paths
        paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        paths.categories.write_text(CATEGORIES, encoding="utf-8")
        paths.emails.mkdir()
        for index, statement_date in enumerate(
            ("2025-12-10", "2026-01-10", "2026-02-10"),
            start=1,
        ):
            digest = format(index, "024x")
            (paths.emails / f"{statement_date}_{digest}.eml").write_bytes(b"test")
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
            paths.transactions,
        )
        application = FamilySpendingApplication(paths)
        application.initialize()
        self.server = create_http_server(application, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def _stop_server(self) -> None:
        """Stop the background HTTP server so temporary test roots can be removed deterministically."""
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def _json_request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        """Issue one local JSON request and normalize both success and HTTP error responses."""
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                return exc.code, body
            finally:
                exc.close()
        with response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, body

    def test_health_query_and_enrichment_patch(self) -> None:
        status, health = self._json_request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health, {"status": "ok"})

        status, listing = self._json_request("/api/transactions")
        self.assertEqual(status, 200)
        transactions = listing["transactions"]
        self.assertIsInstance(transactions, list)
        transaction_id = transactions[0]["id"]

        status, changed = self._json_request(
            f"/api/transactions/{transaction_id}/enrichment",
            method="PATCH",
            payload={"merchant": "测试家电", "note": "HTTP 修改"},
        )
        self.assertEqual(status, 200)
        enrichment = changed["transaction"]["enrichment"]
        self.assertEqual(enrichment["merchant"], "测试家电")
        self.assertEqual(enrichment["category"], "家居家电")
        self.assertEqual(enrichment["note"], "HTTP 修改")

    def test_financial_summary_query_returns_current_projection_without_rebuild(self) -> None:
        expected = json.loads(
            self.paths.spending_statistics.with_name("financial_summary.json").read_text(
                encoding="utf-8"
            )
        )
        with patch(
            "family_spending.application.generate_spending_statistics",
            side_effect=AssertionError("GET must not rebuild projections"),
        ):
            status, body = self._json_request("/api/financial-summary")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"financial_summary": expected})

    def test_feedback_create_list_resolve_and_reopen(self) -> None:
        status, created_body = self._json_request(
            "/api/feedback",
            method="POST",
            payload={
                "content": "总览层级需要调整",
                "context": {
                    "runtime": "desktop_web",
                    "page": "/overview",
                    "workspace": "overview",
                },
            },
        )
        self.assertEqual(status, 201)
        created = created_body["feedback"]
        feedback_id = created["id"]
        self.assertEqual(created["status"], "open")
        self.assertEqual(created["content"], "总览层级需要调整")
        self.assertEqual(created["context"]["runtime"], "desktop_web")
        self.assertTrue(
            (self.paths.transactions.parent / "feedback.jsonl").exists()
        )

        status, listing = self._json_request("/api/feedback")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in listing["feedback"]], [feedback_id])

        status, resolved_body = self._json_request(
            f"/api/feedback/{feedback_id}",
            method="PATCH",
            payload={"status": "resolved"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(resolved_body["feedback"]["status"], "resolved")

        status, reopened_body = self._json_request(
            f"/api/feedback/{feedback_id}",
            method="PATCH",
            payload={"status": "open"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(reopened_body["feedback"]["status"], "open")
        self.assertEqual(reopened_body["feedback"]["content"], created["content"])
        self.assertEqual(reopened_body["feedback"]["context"], created["context"])

    def test_feedback_rejects_invalid_context_and_missing_item(self) -> None:
        status, body = self._json_request(
            "/api/feedback",
            method="POST",
            payload={
                "content": "测试反馈",
                "context": {"runtime": "browser"},
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Feedback runtime", body["error"])

        status, body = self._json_request(
            "/api/feedback/feedback_missing",
            method="PATCH",
            payload={"status": "resolved"},
        )
        self.assertEqual(status, 404)
        self.assertIn("does not exist", body["error"])

    def test_patch_rejects_unknown_fields(self) -> None:
        _, listing = self._json_request("/api/transactions")
        transaction_id = listing["transactions"][0]["id"]
        status, body = self._json_request(
            f"/api/transactions/{transaction_id}/enrichment",
            method="PATCH",
            payload={"unexpected": "value"},
        )
        self.assertEqual(status, 400)
        self.assertIn("Unknown Enrichment fields", body["error"])


if __name__ == "__main__":
    unittest.main()
