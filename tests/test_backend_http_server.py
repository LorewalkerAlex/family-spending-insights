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

from family_spending.backend.application import FamilySpendingApplication
from family_spending.backend.http_server import create_http_server
from family_spending.backend.paths import BackendPaths
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


class BackendHttpServerTests(unittest.TestCase):
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
            scheduled_input_rules=root / "scheduled_input_rules.json",
            feedback=root / "feedback.jsonl",
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
                    transaction_id="cmb_known",
                    transaction_date=date(2026, 1, 2),
                    amount=Decimal("20"),
                    description="支付宝-测试餐饮",
                    source_email="statement.eml",
                    source_index=1,
                ),
                CmbTransaction(
                    transaction_id="cmb_unknown",
                    transaction_date=date(2026, 1, 3),
                    amount=Decimal("30"),
                    description="支付宝-待审核",
                    source_email="statement.eml",
                    source_index=2,
                ),
            ),
            self.paths.transactions,
        )
        self.application = FamilySpendingApplication(self.paths)
        self.application.initialize()
        self.server = create_http_server(self.application, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
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
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()
        with response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_read_routes_use_one_canonical_application_without_hidden_sync(self) -> None:
        with patch(
            "family_spending.backend.pipeline.HouseholdPipeline.sync_sources",
            side_effect=AssertionError("GET must not run Source Sync"),
        ):
            health = self._request("GET", "/api/health")
            financial = self._request("GET", "/api/financial-summary")
            spending = self._request("GET", "/api/spending-statistics")
            categories = self._request("GET", "/api/categories")
            transactions = self._request("GET", "/api/transactions")
            review = self._request("GET", "/api/mapping-reviews")

        self.assertEqual(health, (200, {"status": "ok"}))
        self.assertEqual(financial[0], 200)
        self.assertEqual(financial[1]["financial_summary"]["schema_version"], 1)
        self.assertEqual(spending[0], 200)
        self.assertEqual(spending[1]["spending_statistics"]["schema_version"], 2)
        self.assertEqual(categories, (200, {"categories": ["餐饮美食"]}))
        self.assertEqual(len(transactions[1]["transactions"]), 2)
        self.assertEqual(len(review[1]["mapping_review"]["items"]), 1)

    def test_manual_enrichment_feedback_and_schedule_routes_round_trip(self) -> None:
        status, created = self._request(
            "POST",
            "/api/manual-inputs",
            {
                "type": "income",
                "date": "2026-01-06",
                "amount": "1000",
                "description": "工资-测试",
                "note": "工资",
            },
        )
        self.assertEqual(status, 201)
        source_id = created["manual_input"]["source_record_id"]
        self.assertIsNone(
            created["manual_input"]["transaction"]["enrichment"]["merchant"]
        )
        self.assertEqual(
            created["manual_input"]["transaction"]["enrichment"]["category"],
            "其他收入",
        )

        status, manual_inputs = self._request("GET", "/api/manual-inputs")
        self.assertEqual(status, 200)
        self.assertEqual(len(manual_inputs["manual_inputs"]), 1)

        known_id = next(
            item.transaction.id
            for item in self.application.list_transactions()
            if item.source_record.id == "cmb_known"
        )
        status, enriched = self._request(
            "PATCH",
            f"/api/transactions/{known_id}/enrichment",
            {"note": "HTTP note"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            enriched["transaction"]["enrichment"]["note"],
            "HTTP note",
        )

        status, feedback = self._request(
            "POST",
            "/api/feedback",
            {
                "content": "UI feedback",
                "context": {"runtime": "desktop_web", "page": "overview"},
            },
        )
        self.assertEqual(status, 201)
        feedback_id = feedback["feedback"]["id"]
        status, resolved = self._request(
            "PATCH",
            f"/api/feedback/{feedback_id}",
            {"status": "resolved"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(resolved["feedback"]["status"], "resolved")

        status, scheduled = self._request(
            "POST",
            "/api/scheduled-inputs",
            {
                "type": "income",
                "amount": "15000",
                "description": "工资",
                "next_date": "2099-01-06",
                "note": None,
                "enabled": True,
            },
        )
        self.assertEqual(status, 201)
        rule_id = scheduled["scheduled_input"]["id"]
        status, updated = self._request(
            "PATCH",
            f"/api/scheduled-inputs/{rule_id}",
            {
                "type": "income",
                "amount": "16000",
                "description": "工资",
                "next_date": "2099-02-06",
                "note": None,
                "enabled": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["scheduled_input"]["amount"], "16000")
        self.assertFalse(updated["scheduled_input"]["enabled"])
        status, deleted_rule = self._request(
            "DELETE",
            f"/api/scheduled-inputs/{rule_id}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(deleted_rule["scheduled_input_deletion"]["id"], rule_id)

        status, deleted_manual = self._request(
            "DELETE",
            f"/api/manual-inputs/{source_id}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            deleted_manual["manual_input_deletion"]["source_record_id"],
            source_id,
        )

    def test_mapping_preview_apply_and_validation_errors_stay_transport_only(self) -> None:
        status, preview_body = self._request(
            "POST",
            "/api/mapping-reviews/preview",
            {
                "description": "支付宝-待审核",
                "merchant": "测试餐饮",
                "category": "餐饮美食",
            },
        )
        self.assertEqual(status, 200)
        token = preview_body["preview"]["token"]
        status, applied = self._request(
            "POST",
            "/api/mapping-reviews/apply",
            {
                "description": "支付宝-待审核",
                "merchant": "测试餐饮",
                "category": "餐饮美食",
                "preview_token": token,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["mapping_review"]["merchant"], "测试餐饮")

        status, error = self._request(
            "POST",
            "/api/manual-inputs",
            {
                "type": "expense",
                "date": "2026-01-09",
                "amount": "10",
                "description": "测试",
                "unexpected": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Unknown Manual Input fields", error["error"])

    def test_missing_projection_returns_conflict_not_transport_rebuild(self) -> None:
        self.paths.spending_statistics.unlink()
        status, body = self._request("GET", "/api/spending-statistics")
        self.assertEqual(status, 409)
        self.assertIn("does not exist", body["error"])


if __name__ == "__main__":
    unittest.main()
