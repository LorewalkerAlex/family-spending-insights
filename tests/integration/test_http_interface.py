from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from family_spending.config import AppConfig, StorageConfig
from family_spending.domain.mapping import MappingCatalog
from family_spending.interfaces.http.server import FamilySpendingHttpServer, create_http_server
from family_spending.runtime.composition import compose_runtime


class HttpInterfaceIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name).resolve() / "household"
        self.config = AppConfig(storage=StorageConfig(root))
        seed = compose_runtime(self.config)
        seed.mapping_store.replace(
            MappingCatalog(
                description_to_merchant={"known": "Known Merchant"},
                merchant_to_category={"Known Merchant": "餐饮美食"},
                categories=frozenset({"餐饮美食"}),
            )
        )
        self.components = compose_runtime(self.config)
        self.application = self.components.application
        self.server = create_http_server(self.application, port=0)
        self.assertIsInstance(self.server, FamilySpendingHttpServer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.server.server_address[:2]
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

    def test_options_and_all_read_routes_keep_the_existing_transport_contract(self) -> None:
        request = Request(f"{self.base_url}/api/health", method="OPTIONS")
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
            self.assertIn("PATCH", response.headers["Access-Control-Allow-Methods"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")

        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-08-01",
            amount="12",
            description="known",
        )
        transaction_id = created.transaction.transaction.id
        feedback = self.application.create_feedback(content="read route")

        routes = {
            "/api/transactions": "transactions",
            f"/api/transactions/{transaction_id}": "transaction",
            "/api/manual-descriptions": "descriptions",
            "/api/mapping-reviews": "mapping_review",
            "/api/feedback": "feedback",
            "/api/scheduled-inputs": "scheduled_inputs",
        }
        for path, key in routes.items():
            with self.subTest(path=path):
                status, body = self._request("GET", path)
                self.assertEqual(status, 200)
                self.assertIn(key, body)
        self.assertEqual(self._request("GET", "/api/feedback")[1]["feedback"][0]["id"], feedback.id)

    def test_read_routes_and_strict_manual_transport_shape(self) -> None:
        status, created = self._request(
            "POST",
            "/api/manual-inputs",
            {
                "type": "income",
                "date": "2026-08-01",
                "amount": "1000",
                "description": "salary",
                "note": "income note",
            },
        )
        self.assertEqual(status, 201)
        manual = created["manual_input"]
        self.assertEqual(set(manual), {"source_record_id", "action", "transaction"})
        evidence_handle = manual["source_record_id"]
        self.assertTrue(str(evidence_handle).startswith("manual_"))
        self.assertNotEqual(
            evidence_handle,
            manual["transaction"]["source"]["id"],
        )

        self.assertEqual(self._request("GET", "/api/health"), (200, {"status": "ok"}))
        self.assertEqual(self._request("GET", "/api/categories"), (200, {"categories": ["餐饮美食"]}))
        self.assertEqual(self._request("GET", "/api/financial-summary")[1]["financial_summary"]["schema_version"], 1)
        self.assertEqual(self._request("GET", "/api/spending-statistics")[1]["spending_statistics"]["schema_version"], 2)

        status, listed = self._request("GET", "/api/manual-inputs")
        self.assertEqual(status, 200)
        record = listed["manual_inputs"][0]
        self.assertEqual(record["source_record_id"], evidence_handle)
        self.assertEqual(record["note"], "income note")
        self.assertEqual(
            set(record),
            {
                "source_record_id",
                "transaction_id",
                "source_role",
                "type",
                "date",
                "amount",
                "currency",
                "description",
                "note",
                "transaction",
            },
        )

        status, correction = self._request(
            "POST",
            f"/api/manual-inputs/{evidence_handle}/corrections",
            {
                "type": "income",
                "date": "2026-08-02",
                "amount": "1100",
                "description": "salary corrected",
                "note": "corrected",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(correction["manual_input_correction"]["replaced_source_record_id"], evidence_handle)
        self.assertEqual(correction["manual_input_correction"]["manual_input"]["source_record_id"], evidence_handle)

        status, deletion = self._request("DELETE", f"/api/manual-inputs/{evidence_handle}")
        self.assertEqual(status, 200)
        self.assertEqual(deletion["manual_input_deletion"]["source_record_id"], evidence_handle)

    def test_enrichment_mapping_feedback_and_schedule_round_trip(self) -> None:
        _, known = self._request(
            "POST",
            "/api/manual-inputs",
            {
                "type": "expense",
                "date": "2026-08-03",
                "amount": "20",
                "description": "known",
            },
        )
        transaction_id = known["manual_input"]["transaction"]["id"]
        status, enriched = self._request(
            "PATCH",
            f"/api/transactions/{transaction_id}/enrichment",
            {"note": "HTTP note"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(enriched["transaction"]["enrichment"]["note"], "HTTP note")

        self._request(
            "POST",
            "/api/manual-inputs",
            {
                "type": "expense",
                "date": "2026-08-04",
                "amount": "30",
                "description": "needs review",
            },
        )
        status, preview = self._request(
            "POST",
            "/api/mapping-reviews/preview",
            {
                "description": "needs review",
                "merchant": "Known Merchant",
                "category": "餐饮美食",
            },
        )
        self.assertEqual(status, 200)
        token = preview["preview"]["token"]
        status, applied = self._request(
            "POST",
            "/api/mapping-reviews/apply",
            {
                "description": "needs review",
                "merchant": "Known Merchant",
                "category": "餐饮美食",
                "preview_token": token,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["mapping_review"]["merchant"], "Known Merchant")
        stale_status, stale = self._request(
            "POST",
            "/api/mapping-reviews/apply",
            {
                "description": "needs review",
                "merchant": "Known Merchant",
                "category": "餐饮美食",
                "preview_token": token,
            },
        )
        self.assertEqual(stale_status, 409)
        self.assertIn("already mapped", stale["error"])

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
                "description": "salary",
                "next_date": "2099-01-06",
                "note": None,
                "enabled": True,
            },
        )
        self.assertEqual(status, 201)
        rule = scheduled["scheduled_input"]
        self.assertEqual(rule["next_date"], "2099-01-06")
        self.assertIsNone(rule["last_occurrence_date"])
        rule_id = rule["id"]
        status, updated = self._request(
            "PATCH",
            f"/api/scheduled-inputs/{rule_id}",
            {
                "type": "income",
                "amount": "16000",
                "description": "salary",
                "next_date": "2099-02-06",
                "note": None,
                "enabled": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["scheduled_input"]["amount"], "16000")
        self.assertEqual(self._request("POST", "/api/scheduled-inputs/run-due")[0], 200)
        status, deleted = self._request("DELETE", f"/api/scheduled-inputs/{rule_id}")
        self.assertEqual(status, 200)
        self.assertEqual(deleted["scheduled_input_deletion"]["id"], rule_id)

    def test_schedule_last_run_metadata_and_transport_errors(self) -> None:
        rule = self.application.create_scheduled_input(
            transaction_type="income",
            amount="200",
            description="monthly income",
            next_date="2026-01-10",
            as_of=__import__("datetime").date(2026, 1, 10),
        )
        status, listed = self._request("GET", "/api/scheduled-inputs")
        self.assertEqual(status, 200)
        view = next(item for item in listed["scheduled_inputs"] if item["id"] == rule.id)
        self.assertEqual(view["last_occurrence_date"], "2026-01-10")
        self.assertIsNotNone(view["last_source_record_id"])
        self.assertIsNotNone(view["last_transaction_id"])
        self.assertEqual(view["last_action"], "created")
        self.assertEqual(view["next_date"], "2026-02-10")

        # Cursor loss recovers the stable occurrence and preserves the true historical action.
        self.components.schedule_store.replace_execution(())
        recovered = self.application.run_due_scheduled_inputs(
            __import__("datetime").date(2026, 1, 10)
        )
        self.assertEqual(recovered.occurrences[0].action, "recovered")
        _, relisted = self._request("GET", "/api/scheduled-inputs")
        recovered_view = next(
            item for item in relisted["scheduled_inputs"] if item["id"] == rule.id
        )
        self.assertEqual(recovered_view["last_action"], "recovered")

        status, error = self._request(
            "POST",
            "/api/manual-inputs",
            {
                "type": "expense",
                "date": "2026-08-05",
                "amount": "10",
                "description": "bad",
                "unexpected": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Unknown Manual Input fields", error["error"])
        self.assertEqual(self._request("GET", "/api/not-found"), (404, {"error": "Route not found"}))


if __name__ == "__main__":
    unittest.main()
