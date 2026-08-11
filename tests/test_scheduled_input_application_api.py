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
    ApplicationNotFoundError,
    ApplicationPaths,
    ApplicationValidationError,
    FamilySpendingApplication,
)
from family_spending.http_api import create_http_server
from family_spending.ingestion.cmb_email_transactions import (
    CmbTransaction,
    write_transactions_csv,
)
from family_spending.manual_input import submit_manual_input
from family_spending.manual_source import (
    create_manual_source_entry,
    read_manual_source_entries,
)
from family_spending.scheduled_input import (
    ScheduledInputError,
    create_scheduled_input_rule,
    occurrence_source_record_id,
    read_scheduled_input_rules,
    write_scheduled_input_rules,
)

MERCHANTS = """\
测试餐饮:
  - 支付宝-测试餐饮
"""

CATEGORIES = """\
餐饮美食:
  - 测试餐饮
"""


def build_paths(root: Path) -> ApplicationPaths:
    """Create isolated state so scheduled execution never touches the developer's real data."""
    paths = ApplicationPaths(
        transactions=root / "transactions.csv",
        manual_source=root / "manual_source_records.jsonl",
        source_links=root / "transaction_source_links.jsonl",
        enrichment_state=root / "enrichment_state.jsonl",
        merchants=root / "merchants.yaml",
        categories=root / "categories.yaml",
        spending_statistics=root / "reports" / "spending_statistics.json",
        emails=root / "emails",
        scheduled_input_rules=root / "scheduled_input_rules.json",
    )
    paths.merchants.write_text(MERCHANTS, encoding="utf-8")
    paths.categories.write_text(CATEGORIES, encoding="utf-8")
    paths.emails.mkdir()
    for index, statement_date in enumerate(
        ("2025-12-10", "2026-01-10", "2026-02-10", "2026-03-10", "2026-04-10"),
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


class ScheduledInputApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.paths = build_paths(Path(self.temp_dir.name))
        self.application = FamilySpendingApplication(self.paths)
        self.application.initialize()

    def test_application_paths_derive_scheduled_storage_beside_custom_transactions(self) -> None:
        root = Path(self.temp_dir.name) / "other"
        paths = ApplicationPaths(transactions=root / "transactions.csv")
        self.assertEqual(paths.scheduled_input_rules, root / "scheduled_input_rules.json")

    def test_rule_storage_round_trips_and_removes_empty_file(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("88.50"),
            description="固定房租",
            next_date=date(2026, 9, 11),
            note="自动录入",
            rule_id="schedule_test",
        )

        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        self.assertEqual(read_scheduled_input_rules(self.paths.scheduled_input_rules), (rule,))
        write_scheduled_input_rules((), self.paths.scheduled_input_rules)
        self.assertFalse(self.paths.scheduled_input_rules.exists())

    def test_rule_rejects_month_end_recurrence(self) -> None:
        with self.assertRaisesRegex(ScheduledInputError, "1-28"):
            create_scheduled_input_rule(
                transaction_type="expense",
                amount=Decimal("88.50"),
                description="月底账单",
                next_date=date(2026, 8, 31),
            )

        with self.assertRaisesRegex(ApplicationValidationError, "1-28"):
            self.application.create_scheduled_input(
                transaction_type="expense",
                amount="88.50",
                description="月底账单",
                next_date="2026-08-31",
                enabled=True,
            )

    def test_create_future_rule_does_not_create_manual_source(self) -> None:
        rule = self.application.create_scheduled_input(
            transaction_type="expense",
            amount="88.50",
            description="固定房租",
            next_date="2099-09-11",
            note="自动录入",
            enabled=True,
        )

        self.assertTrue(rule.id.startswith("schedule_"))
        self.assertEqual(rule.next_date, date(2099, 9, 11))
        self.assertIsNone(rule.last_occurrence_date)
        self.assertEqual(len(self.application.list_scheduled_inputs()), 1)
        self.assertEqual(read_manual_source_entries(self.paths.manual_source), ())

    def test_run_due_catches_up_monthly_occurrences_and_is_idempotent(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("30"),
            description="月度固定支出",
            next_date=date(2026, 1, 11),
            rule_id="schedule_monthly",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)

        first = self.application.run_due_scheduled_inputs(date(2026, 3, 11))
        second = self.application.run_due_scheduled_inputs(date(2026, 3, 11))

        self.assertEqual(
            [item.occurrence_date for item in first.occurrences],
            [date(2026, 1, 11), date(2026, 2, 11), date(2026, 3, 11)],
        )
        self.assertEqual(second.occurrences, ())
        entries = read_manual_source_entries(self.paths.manual_source)
        self.assertEqual(len(entries), 3)
        self.assertEqual([entry.amount for entry in entries], [Decimal("30")] * 3)
        persisted_rule = self.application.list_scheduled_inputs()[0]
        self.assertEqual(persisted_rule.next_date, date(2026, 4, 11))
        self.assertEqual(persisted_rule.last_occurrence_date, date(2026, 3, 11))
        self.assertEqual(persisted_rule.last_transaction_id, first.occurrences[-1].transaction_id)

    def test_stable_occurrence_id_recovers_manual_write_without_duplicate(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("44"),
            description="恢复测试",
            next_date=date(2026, 2, 12),
            rule_id="schedule_recovery",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        source_id = occurrence_source_record_id(rule.id, rule.next_date)
        entry = create_manual_source_entry(
            transaction_type="expense",
            transaction_date=rule.next_date,
            amount=rule.amount,
            description=rule.description,
            source_record_id=source_id,
        )
        submitted = submit_manual_input(
            entry,
            transactions_path=self.paths.transactions,
            manual_source_path=self.paths.manual_source,
            source_links_path=self.paths.source_links,
            merchants_path=self.paths.merchants,
            categories_path=self.paths.categories,
            output_path=self.paths.spending_statistics,
            emails_dir=self.paths.emails,
            enrichment_state_path=self.paths.enrichment_state,
        )

        result = self.application.run_due_scheduled_inputs(date(2026, 2, 12))

        self.assertEqual(len(result.occurrences), 1)
        self.assertEqual(result.occurrences[0].action, "recovered")
        self.assertEqual(result.occurrences[0].transaction_id, submitted.transaction_id)
        self.assertEqual(len(read_manual_source_entries(self.paths.manual_source)), 1)
        self.assertEqual(
            self.application.list_scheduled_inputs()[0].next_date,
            date(2026, 3, 12),
        )

    def test_failed_multi_occurrence_run_restores_rules_and_downstream_files(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("55"),
            description="回滚测试",
            next_date=date(2026, 1, 13),
            rule_id="schedule_rollback",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        baseline = {
            path: path.read_bytes() if path.exists() else None
            for path in (
                self.paths.scheduled_input_rules,
                self.paths.manual_source,
                self.paths.source_links,
                self.paths.enrichment_state,
                self.paths.spending_statistics,
            )
        }
        from family_spending import scheduled_input as scheduled_module

        original_submit = scheduled_module.submit_manual_input
        call_count = 0

        def fail_second(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("scheduled persistence failed")
            return original_submit(*args, **kwargs)

        with patch(
            "family_spending.scheduled_input.submit_manual_input",
            side_effect=fail_second,
        ):
            with self.assertRaisesRegex(OSError, "scheduled persistence failed"):
                self.application.run_due_scheduled_inputs(date(2026, 2, 13))

        for path, contents in baseline.items():
            if contents is None:
                self.assertFalse(path.exists(), path)
            else:
                self.assertEqual(path.read_bytes(), contents, path)

    def test_update_changes_only_future_rule_and_delete_keeps_generated_history(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("60"),
            description="旧规则描述",
            next_date=date(2026, 1, 14),
            rule_id="schedule_edit",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        run = self.application.run_due_scheduled_inputs(date(2026, 1, 14))
        generated_source_id = run.occurrences[0].source_record_id

        updated = self.application.update_scheduled_input(
            "schedule_edit",
            transaction_type="expense",
            amount="70",
            description="新规则描述",
            next_date="2099-02-14",
            note="只影响未来",
            enabled=False,
        )

        self.assertEqual(updated.amount, Decimal("70"))
        self.assertFalse(updated.enabled)
        historical = next(
            entry
            for entry in read_manual_source_entries(self.paths.manual_source)
            if entry.id == generated_source_id
        )
        self.assertEqual(historical.amount, Decimal("60"))
        self.assertEqual(historical.description, "旧规则描述")

        deleted = self.application.delete_scheduled_input("schedule_edit")
        self.assertEqual(deleted.id, "schedule_edit")
        self.assertEqual(self.application.list_scheduled_inputs(), ())
        self.assertFalse(self.paths.scheduled_input_rules.exists())
        self.assertTrue(
            any(
                entry.id == generated_source_id
                for entry in read_manual_source_entries(self.paths.manual_source)
            )
        )

    def test_update_rejects_next_date_before_last_generated_occurrence(self) -> None:
        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("61"),
            description="单调日期",
            next_date=date(2026, 1, 15),
            rule_id="schedule_monotonic",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)
        self.application.run_due_scheduled_inputs(date(2026, 1, 15))

        with self.assertRaisesRegex(ApplicationValidationError, "after the last generated"):
            self.application.update_scheduled_input(
                "schedule_monotonic",
                transaction_type="expense",
                amount="61",
                description="单调日期",
                next_date="2026-01-14",
                enabled=True,
            )

    def test_due_rule_creation_failure_restores_pre_command_state(self) -> None:
        baseline = {
            path: path.read_bytes() if path.exists() else None
            for path in (
                self.paths.scheduled_input_rules,
                self.paths.manual_source,
                self.paths.source_links,
                self.paths.enrichment_state,
                self.paths.spending_statistics,
            )
        }
        with patch(
            "family_spending.scheduled_input.submit_manual_input",
            side_effect=OSError("scheduled create failed"),
        ):
            with self.assertRaisesRegex(OSError, "scheduled create failed"):
                self.application.create_scheduled_input(
                    transaction_type="expense",
                    amount="62",
                    description="创建回滚",
                    next_date="2026-01-16",
                    enabled=True,
                )

        for path, contents in baseline.items():
            if contents is None:
                self.assertFalse(path.exists(), path)
            else:
                self.assertEqual(path.read_bytes(), contents, path)

    def test_delete_missing_rule_returns_not_found(self) -> None:
        with self.assertRaisesRegex(ApplicationNotFoundError, "does not exist"):
            self.application.delete_scheduled_input("schedule_missing")

    def test_initialize_runs_occurrence_due_today(self) -> None:
        fixed_today = date(2026, 3, 18)

        class FixedDate(date):
            @classmethod
            def today(cls) -> date:
                return fixed_today

        rule = create_scheduled_input_rule(
            transaction_type="expense",
            amount=Decimal("66"),
            description="启动自动执行",
            next_date=fixed_today,
            rule_id="schedule_startup",
        )
        write_scheduled_input_rules((rule,), self.paths.scheduled_input_rules)

        with patch("family_spending.application.date", FixedDate):
            self.application.initialize()

        persisted = self.application.list_scheduled_inputs()[0]
        self.assertEqual(persisted.last_occurrence_date, fixed_today)
        self.assertTrue(
            any(
                entry.id == persisted.last_source_record_id
                for entry in read_manual_source_entries(self.paths.manual_source)
            )
        )


class ScheduledInputHttpApiTests(unittest.TestCase):
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

    def test_rule_crud_and_run_due_endpoints(self) -> None:
        status, body = self._json_request(
            "/api/scheduled-inputs",
            method="POST",
            payload={
                "type": "expense",
                "amount": "99",
                "description": "API 月度规则",
                "note": "测试",
                "next_date": "2099-08-15",
                "enabled": True,
            },
        )
        self.assertEqual(status, 201)
        rule_id = body["scheduled_input"]["id"]

        status, body = self._json_request("/api/scheduled-inputs")
        self.assertEqual(status, 200)
        self.assertEqual(body["scheduled_inputs"][0]["id"], rule_id)

        status, body = self._json_request(
            f"/api/scheduled-inputs/{rule_id}",
            method="PATCH",
            payload={
                "type": "income",
                "amount": "100",
                "description": "API 修改规则",
                "note": None,
                "next_date": "2099-09-15",
                "enabled": False,
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["scheduled_input"]["enabled"])
        self.assertEqual(body["scheduled_input"]["type"], "income")

        status, body = self._json_request(
            "/api/scheduled-inputs/run-due",
            method="POST",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["scheduled_input_run"]["generated_count"], 0)

        status, body = self._json_request(
            f"/api/scheduled-inputs/{rule_id}",
            method="DELETE",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["scheduled_input_deletion"]["id"], rule_id)

    def test_rule_endpoint_rejects_month_end_and_missing_rule(self) -> None:
        status, body = self._json_request(
            "/api/scheduled-inputs",
            method="POST",
            payload={
                "type": "expense",
                "amount": "99",
                "description": "月底规则",
                "next_date": "2026-08-31",
                "enabled": True,
            },
        )
        self.assertEqual(status, 400)
        self.assertIn("1-28", body["error"])

        status, body = self._json_request(
            "/api/scheduled-inputs/schedule_missing",
            method="DELETE",
        )
        self.assertEqual(status, 404)
        self.assertIn("does not exist", body["error"])


if __name__ == "__main__":
    unittest.main()
