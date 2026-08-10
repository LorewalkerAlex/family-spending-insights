from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
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
测试购物:
  - 支付宝-测试购物
"""

CATEGORIES = """\
餐饮美食:
  - 测试餐饮
综合购物:
  - 测试购物
"""


class MappingReviewHttpApiTests(unittest.TestCase):
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
            overrides=root / "transaction_category_overrides.jsonl",
            spending_statistics=root / "reports" / "spending_statistics.json",
            emails=root / "emails",
        )
        paths.merchants.write_text(MERCHANTS, encoding="utf-8")
        paths.categories.write_text(CATEGORIES, encoding="utf-8")
        paths.overrides.write_text("", encoding="utf-8")
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
                    transaction_id="cmb_unmapped",
                    transaction_date=date(2026, 1, 2),
                    amount=Decimal("20"),
                    description="支付宝-HTTP待审核",
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

    def test_mapping_review_get_preview_and_apply(self) -> None:
        status, listing = self._json_request("/api/mapping-reviews")
        self.assertEqual(status, 200)
        workspace = listing["mapping_review"]
        self.assertEqual(workspace["items"][0]["description"], "支付宝-HTTP待审核")
        self.assertEqual(workspace["items"][0]["transaction_count"], 1)
        self.assertEqual(
            {merchant["name"] for merchant in workspace["merchants"]},
            {"测试餐饮", "测试购物"},
        )

        status, preview_payload = self._json_request(
            "/api/mapping-reviews/preview",
            method="POST",
            payload={
                "description": "支付宝-HTTP待审核",
                "merchant": "测试餐饮",
                "category": "餐饮美食",
            },
        )
        self.assertEqual(status, 200)
        preview = preview_payload["preview"]
        self.assertEqual(preview["description_affected_transaction_count"], 1)
        self.assertFalse(preview["is_new_merchant"])

        status, applied = self._json_request(
            "/api/mapping-reviews/apply",
            method="POST",
            payload={
                "description": "支付宝-HTTP待审核",
                "merchant": "测试餐饮",
                "category": "餐饮美食",
                "preview_token": preview["token"],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["mapping_review"]["merchant"], "测试餐饮")

        _, refreshed = self._json_request("/api/mapping-reviews")
        self.assertEqual(refreshed["mapping_review"]["items"], [])
        _, transactions = self._json_request("/api/transactions")
        self.assertEqual(transactions["transactions"][0]["enrichment"]["merchant"], "测试餐饮")

    def test_mapping_review_rejects_unknown_fields_and_unconfirmed_new_merchant(self) -> None:
        status, body = self._json_request(
            "/api/mapping-reviews/preview",
            method="POST",
            payload={
                "description": "支付宝-HTTP待审核",
                "merchant": "新商户",
                "category": "餐饮美食",
                "unexpected": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Unknown Mapping Review preview fields", body["error"])

        _, preview_payload = self._json_request(
            "/api/mapping-reviews/preview",
            method="POST",
            payload={
                "description": "支付宝-HTTP待审核",
                "merchant": "新商户",
                "category": "餐饮美食",
            },
        )
        status, body = self._json_request(
            "/api/mapping-reviews/apply",
            method="POST",
            payload={
                "description": "支付宝-HTTP待审核",
                "merchant": "新商户",
                "category": "餐饮美食",
                "preview_token": preview_payload["preview"]["token"],
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("confirm_new_merchant", body["error"])


if __name__ == "__main__":
    unittest.main()