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

from family_spending.application import (
    ApplicationConflictError,
    ApplicationPaths,
    FamilySpendingApplication,
)
from family_spending.enrichment import UNCLASSIFIED_CATEGORY
from family_spending.http_api import create_http_server
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    read_transactions_csv,
    write_transactions_csv,
)
from family_spending.manual_source import read_manual_source_entries

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


def build_paths(root: Path) -> ApplicationPaths:
    """Create one isolated Application path set using only the current Mapping inputs."""
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
    return paths


class ManualInputApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.paths = build_paths(Path(self.temp_dir.name))
        self.application = FamilySpendingApplication(self.paths)
        self.application.initialize()
        self.cmb_transaction_id = self.application.list_transactions()[0].transaction.id

    def test_new_manual_description_is_source_fact_and_stays_unclassified_without_mapping(self) -> None:
        result = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="  公司楼下新咖啡店  ",
            note="现金购买",
        )

        self.assertEqual(result.action, "created")
        self.assertTrue(result.source_record_id.startswith("manual_"))
        self.assertEqual(result.transaction.source_record.description, "公司楼下新咖啡店")
        self.assertIsNone(result.transaction.enrichment.merchant_name)
        self.assertEqual(result.transaction.enrichment.category, UNCLASSIFIED_CATEGORY)
        self.assertEqual(result.transaction.enrichment.note, "现金购买")

        entries = read_manual_source_entries(self.paths.manual_source)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].description, "公司楼下新咖啡店")
        self.assertIsNone(entries[0].merchant_name)
        self.assertIsNone(entries[0].category)
        persisted = json.loads(self.paths.manual_source.read_text(encoding="utf-8"))
        self.assertEqual(persisted["description"], "公司楼下新咖啡店")
        self.assertNotIn("merchant", persisted)
        self.assertNotIn("category", persisted)

        projection = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))
        january = next(month for month in projection["months"] if month["month"] == "2026-01")
        self.assertEqual(january["total_spending_minor"], 5550)

    def test_existing_description_mapping_supplies_merchant_and_default_category(self) -> None:
        result = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="支付宝-测试家电",
        )

        self.assertEqual(result.transaction.source_record.description, "支付宝-测试家电")
        self.assertEqual(result.transaction.enrichment.merchant_name, "测试家电")
        self.assertEqual(result.transaction.enrichment.category, "家居家电")
        entry = read_manual_source_entries(self.paths.manual_source)[0]
        self.assertIsNone(entry.merchant_name)
        self.assertIsNone(entry.category)

    def test_manual_descriptions_are_distinct_and_newest_first(self) -> None:
        for when, amount, description in (
            ("2026-01-03", "31", "现金早餐"),
            ("2026-01-04", "32", "现金房租"),
            ("2026-01-05", "33", "现金早餐"),
        ):
            self.application.create_manual_input(
                transaction_type="expense",
                transaction_date=when,
                amount=amount,
                description=description,
            )

        self.assertEqual(
            self.application.list_manual_descriptions(),
            ("现金早餐", "现金房租"),
        )

    def test_create_manual_input_reuses_unique_existing_cmb_transaction(self) -> None:
        result = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="先前手工记录",
        )

        self.assertEqual(result.action, "matched")
        self.assertEqual(result.transaction.transaction.id, self.cmb_transaction_id)
        self.assertEqual(result.transaction.enrichment.merchant_name, "测试餐饮")
        self.assertEqual(result.transaction.enrichment.note, "先前手工记录")
        self.assertEqual(len(self.application.list_transactions()), 1)

    def test_create_manual_input_surfaces_ambiguous_reconciliation_as_conflict(self) -> None:
        existing = read_transactions_csv(self.paths.transactions)
        write_transactions_csv(
            existing
            + (
                CmbTransaction(
                    transaction_id="cmb_ambiguous",
                    transaction_date=date(2026, 1, 3),
                    amount=Decimal("20"),
                    description="未知商户",
                    source_email="statement.eml",
                    source_index=2,
                ),
            ),
            self.paths.transactions,
        )
        links_before = self.paths.source_links.read_bytes()

        with self.assertRaisesRegex(ApplicationConflictError, "multiple existing transactions"):
            self.application.create_manual_input(
                transaction_type="expense",
                transaction_date="2026-01-02",
                amount="20",
                description="手工未知商户",
            )

        self.assertFalse(self.paths.manual_source.exists())
        self.assertEqual(self.paths.source_links.read_bytes(), links_before)

    def test_failed_projection_generation_rolls_back_all_manual_input_files(self) -> None:
        links_before = self.paths.source_links.read_bytes()
        enrichment_before = self.paths.enrichment_state.read_bytes()
        projection_before = self.paths.spending_statistics.read_bytes()

        with patch(
            "family_spending.manual_input.generate_spending_statistics",
            side_effect=OSError("projection generation failed"),
        ):
            with self.assertRaisesRegex(OSError, "projection generation failed"):
                self.application.create_manual_input(
                    transaction_type="expense",
                    transaction_date="2026-01-03",
                    amount="35.50",
                    description="支付宝-测试家电",
                )

        self.assertFalse(self.paths.manual_source.exists())
        self.assertEqual(self.paths.source_links.read_bytes(), links_before)
        self.assertEqual(self.paths.enrichment_state.read_bytes(), enrichment_before)
        self.assertEqual(self.paths.spending_statistics.read_bytes(), projection_before)

    def test_lists_manual_inputs_with_source_relation_and_current_transaction(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
            note="现金",
        )
        matched = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
        )

        items = self.application.list_manual_inputs()
        self.assertEqual([item.entry.id for item in items], [matched.source_record_id, created.source_record_id])
        self.assertEqual(items[0].source_role, "supporting")
        self.assertEqual(items[0].transaction_id, self.cmb_transaction_id)
        self.assertEqual(items[1].source_role, "authoritative")
        self.assertEqual(items[1].entry.description, "现金早餐")

    def test_correct_manual_input_replaces_source_identity_and_rebuilds_manual_only_transaction(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
            note="旧 Note",
        )
        old_transaction_id = created.transaction.transaction.id

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-04",
            amount="42.00",
            description="现金早午餐",
            note="新 Note",
        )

        self.assertEqual(corrected.replaced_source_record_id, created.source_record_id)
        self.assertNotEqual(corrected.manual_input.source_record_id, created.source_record_id)
        self.assertEqual(corrected.manual_input.transaction.transaction.id, old_transaction_id)
        self.assertEqual(corrected.manual_input.transaction.transaction.amount, Decimal("42.00"))
        self.assertEqual(corrected.manual_input.transaction.source_record.description, "现金早午餐")
        self.assertEqual(corrected.manual_input.transaction.enrichment.note, "新 Note")
        entries = read_manual_source_entries(self.paths.manual_source)
        self.assertEqual([item.id for item in entries], [corrected.manual_input.source_record_id])
        self.assertIn(old_transaction_id, [item.transaction.id for item in self.application.list_transactions()])

        projection = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))
        january = next(month for month in projection["months"] if month["month"] == "2026-01")
        self.assertEqual(january["total_spending_minor"], 6200)


    def test_manual_only_correction_preserves_existing_transaction_enrichment_when_identity_stays_same(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(
            transaction_id,
            category="家居家电",
            note="Transaction Workspace Note",
        )

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-04",
            amount="42.00",
            description="现金早午餐",
        )

        self.assertEqual(corrected.manual_input.action, "reused")
        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertEqual(corrected.manual_input.transaction.enrichment.category, "家居家电")
        self.assertEqual(
            corrected.manual_input.transaction.enrichment.note,
            "Transaction Workspace Note",
        )
        self.assertEqual(
            corrected.manual_input.transaction.enrichment.category_source,
            "manual_override",
        )


    def test_manual_only_correction_reapplies_description_mapping_when_enrichment_still_follows_it(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="支付宝-测试家电",
        )
        transaction_id = created.transaction.transaction.id

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-04",
            amount="42.00",
            description="支付宝-测试餐饮",
        )

        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertEqual(corrected.manual_input.transaction.enrichment.merchant_name, "测试餐饮")
        self.assertEqual(corrected.manual_input.transaction.enrichment.default_category, "餐饮美食")
        self.assertEqual(corrected.manual_input.transaction.enrichment.category, "餐饮美食")
        self.assertEqual(
            corrected.manual_input.transaction.enrichment.category_source,
            "merchant_default",
        )

    def test_manual_only_correction_preserves_explicit_merchant_exception(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )
        transaction_id = created.transaction.transaction.id
        self.application.update_enrichment(transaction_id, merchant="测试家电")

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-04",
            amount="42.00",
            description="支付宝-测试餐饮",
        )

        self.assertEqual(corrected.manual_input.transaction.transaction.id, transaction_id)
        self.assertEqual(corrected.manual_input.transaction.enrichment.merchant_name, "测试家电")
        self.assertEqual(corrected.manual_input.transaction.enrichment.category, "家居家电")

    def test_correct_manual_input_can_reconcile_new_source_to_existing_cmb_transaction(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-08",
            amount="35.50",
            description="现金早餐",
        )

        corrected = self.application.correct_manual_input(
            created.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="已核对信用卡",
        )

        self.assertEqual(corrected.manual_input.action, "matched")
        self.assertEqual(corrected.manual_input.transaction.transaction.id, self.cmb_transaction_id)
        self.assertEqual(len(self.application.list_transactions()), 1)
        listed = self.application.list_manual_inputs()
        self.assertEqual(listed[0].source_role, "supporting")
        self.assertEqual(listed[0].transaction_id, self.cmb_transaction_id)

    def test_correction_without_note_does_not_overwrite_current_enrichment_note(self) -> None:
        matched = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="初始 Note",
        )
        self.application.update_enrichment(
            self.cmb_transaction_id,
            note="Transaction Workspace Note",
        )

        corrected = self.application.correct_manual_input(
            matched.source_record_id,
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
        )

        self.assertEqual(corrected.manual_input.transaction.transaction.id, self.cmb_transaction_id)
        self.assertEqual(
            corrected.manual_input.transaction.enrichment.note,
            "Transaction Workspace Note",
        )
        source_entry = read_manual_source_entries(self.paths.manual_source)[0]
        self.assertEqual(source_entry.note, "初始 Note")

    def test_delete_manual_only_input_removes_unbacked_transaction_and_projection_amount(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )

        deletion = self.application.delete_manual_input(created.source_record_id)

        self.assertTrue(deletion.transaction_removed)
        self.assertEqual(deletion.transaction_id, created.transaction.transaction.id)
        self.assertEqual(len(self.application.list_transactions()), 1)
        self.assertEqual(self.application.list_manual_inputs(), ())
        self.assertFalse(self.paths.manual_source.exists())
        projection = json.loads(self.paths.spending_statistics.read_text(encoding="utf-8"))
        january = next(month for month in projection["months"] if month["month"] == "2026-01")
        self.assertEqual(january["total_spending_minor"], 2000)

    def test_delete_supporting_manual_input_preserves_cmb_transaction(self) -> None:
        matched = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-02",
            amount="20",
            description="支付宝-测试餐饮",
            note="先前手工记录",
        )

        deletion = self.application.delete_manual_input(matched.source_record_id)

        self.assertFalse(deletion.transaction_removed)
        self.assertEqual(deletion.transaction_id, self.cmb_transaction_id)
        self.assertEqual(len(self.application.list_transactions()), 1)
        self.assertEqual(self.application.list_manual_inputs(), ())

    def test_failed_manual_correction_rolls_back_source_links_enrichment_and_projection(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )
        manual_before = self.paths.manual_source.read_bytes()
        links_before = self.paths.source_links.read_bytes()
        enrichment_before = self.paths.enrichment_state.read_bytes()
        projection_before = self.paths.spending_statistics.read_bytes()

        with patch(
            "family_spending.manual_input.generate_spending_statistics",
            side_effect=OSError("correction projection failed"),
        ):
            with self.assertRaisesRegex(OSError, "correction projection failed"):
                self.application.correct_manual_input(
                    created.source_record_id,
                    transaction_type="expense",
                    transaction_date="2026-01-04",
                    amount="42",
                    description="修正早餐",
                )

        self.assertEqual(self.paths.manual_source.read_bytes(), manual_before)
        self.assertEqual(self.paths.source_links.read_bytes(), links_before)
        self.assertEqual(self.paths.enrichment_state.read_bytes(), enrichment_before)
        self.assertEqual(self.paths.spending_statistics.read_bytes(), projection_before)


    def test_failed_manual_deletion_rolls_back_source_links_enrichment_and_projection(self) -> None:
        created = self.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )
        manual_before = self.paths.manual_source.read_bytes()
        links_before = self.paths.source_links.read_bytes()
        enrichment_before = self.paths.enrichment_state.read_bytes()
        projection_before = self.paths.spending_statistics.read_bytes()

        with patch(
            "family_spending.manual_input.generate_spending_statistics",
            side_effect=OSError("deletion projection failed"),
        ):
            with self.assertRaisesRegex(OSError, "deletion projection failed"):
                self.application.delete_manual_input(created.source_record_id)

        self.assertEqual(self.paths.manual_source.read_bytes(), manual_before)
        self.assertEqual(self.paths.source_links.read_bytes(), links_before)
        self.assertEqual(self.paths.enrichment_state.read_bytes(), enrichment_before)
        self.assertEqual(self.paths.spending_statistics.read_bytes(), projection_before)



class ManualInputHttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.paths = build_paths(Path(self.temp_dir.name))
        application = FamilySpendingApplication(self.paths)
        application.initialize()
        self.server = create_http_server(application, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def _stop_server(self) -> None:
        """Stop the local server before the temporary directory is cleaned up."""
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
        """Issue one local JSON request and return a normalized status/body pair."""
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

    def test_manual_descriptions_endpoint_returns_source_native_history(self) -> None:
        self.server.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
        )

        status, body = self._json_request("/api/manual-descriptions")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"descriptions": ["现金早餐"]})

    def test_post_manual_input_uses_description_mapping_and_persists_raw_source(self) -> None:
        status, body = self._json_request(
            "/api/manual-inputs",
            method="POST",
            payload={
                "type": "expense",
                "date": "2026-01-03",
                "amount": "35.50",
                "description": "支付宝-测试家电",
                "note": "Dashboard 录入",
            },
        )

        self.assertEqual(status, 201)
        result = body["manual_input"]
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["transaction"]["source"]["description"], "支付宝-测试家电")
        self.assertEqual(result["transaction"]["enrichment"]["merchant"], "测试家电")
        self.assertEqual(result["transaction"]["enrichment"]["category"], "家居家电")
        self.assertEqual(result["transaction"]["enrichment"]["note"], "Dashboard 录入")
        self.assertEqual(len(read_manual_source_entries(self.paths.manual_source)), 1)

    def test_post_manual_input_returns_conflict_for_ambiguous_match(self) -> None:
        existing = read_transactions_csv(self.paths.transactions)
        write_transactions_csv(
            existing
            + (
                CmbTransaction(
                    transaction_id="cmb_ambiguous",
                    transaction_date=date(2026, 1, 3),
                    amount=Decimal("20"),
                    description="未知商户",
                    source_email="statement.eml",
                    source_index=2,
                ),
            ),
            self.paths.transactions,
        )

        status, body = self._json_request(
            "/api/manual-inputs",
            method="POST",
            payload={
                "type": "expense",
                "date": "2026-01-02",
                "amount": "20",
                "description": "手工未知商户",
            },
        )

        self.assertEqual(status, 409)
        self.assertIn("multiple existing transactions", body["error"])
        self.assertFalse(self.paths.manual_source.exists())

    def test_post_manual_input_rejects_old_enrichment_fields_and_missing_description(self) -> None:
        status, body = self._json_request(
            "/api/manual-inputs",
            method="POST",
            payload={
                "type": "expense",
                "date": "2026-01-03",
                "amount": "35.50",
                "description": "测试",
                "merchant": "不应直接输入",
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("Unknown Manual Input fields", body["error"])

        status, body = self._json_request(
            "/api/manual-inputs",
            method="POST",
            payload={"type": "expense", "date": "2026-01-03", "amount": "35.50"},
        )
        self.assertEqual(status, 400)
        self.assertIn("missing required fields", body["error"])
        self.assertIn("description", body["error"])

    def test_manual_input_management_endpoints_list_correct_and_delete(self) -> None:
        created = self.server.application.create_manual_input(
            transaction_type="expense",
            transaction_date="2026-01-03",
            amount="35.50",
            description="现金早餐",
            note="旧 Note",
        )

        status, body = self._json_request("/api/manual-inputs")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["manual_inputs"]), 1)
        self.assertEqual(body["manual_inputs"][0]["source_record_id"], created.source_record_id)
        self.assertEqual(body["manual_inputs"][0]["source_role"], "authoritative")

        status, body = self._json_request(
            f"/api/manual-inputs/{created.source_record_id}/corrections",
            method="POST",
            payload={
                "type": "expense",
                "date": "2026-01-04",
                "amount": "42.00",
                "description": "修正早餐",
                "note": "新 Note",
            },
        )
        self.assertEqual(status, 200)
        correction = body["manual_input_correction"]
        self.assertEqual(correction["replaced_source_record_id"], created.source_record_id)
        new_source_id = correction["manual_input"]["source_record_id"]
        self.assertNotEqual(new_source_id, created.source_record_id)

        status, body = self._json_request(
            f"/api/manual-inputs/{new_source_id}",
            method="DELETE",
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["manual_input_deletion"]["transaction_removed"])
        self.assertEqual(read_manual_source_entries(self.paths.manual_source), ())
        self.assertFalse(self.paths.manual_source.exists())

    def test_manual_input_correction_returns_404_for_missing_source_record(self) -> None:
        status, body = self._json_request(
            "/api/manual-inputs/manual_missing/corrections",
            method="POST",
            payload={
                "type": "expense",
                "date": "2026-01-04",
                "amount": "42.00",
                "description": "修正早餐",
            },
        )
        self.assertEqual(status, 404)
        self.assertIn("does not exist", body["error"])


if __name__ == "__main__":
    unittest.main()
