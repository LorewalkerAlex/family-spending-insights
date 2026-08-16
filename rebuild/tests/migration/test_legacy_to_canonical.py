from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import yaml

from family_spending.config import AppConfig, CmbEmailSourceConfig, SourceConfig, StorageConfig
from family_spending.domain.enrichment import EnrichmentDecision, resolve_enrichments
from family_spending.domain.mapping import MappingCatalog
from family_spending.domain.transaction import SourceLink, rebuild_transactions_from_source_links
from family_spending.persistence.filesystem.enrichment_store import FilesystemEnrichmentDecisionStore
from family_spending.persistence.filesystem.layout import StorageLayout
from family_spending.persistence.filesystem.schedule_store import FilesystemScheduleStore
from family_spending.projections.financial import build_financial_projection
from family_spending.projections.spending import build_spending_projection
from family_spending.runtime.composition import compose_runtime
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence
from family_spending.sources.cmb_email.parser import parse_cmb_email
from family_spending.sources.manual.model import ManualEvidence, manual_evidence_to_source_record
from rebuild.migration.execute import MigrationExecutionError, execute_migration
from rebuild.migration.legacy import (
    legacy_cmb_source_id,
    legacy_schedule_occurrence_source_id,
)
from rebuild.migration.plan import MigrationPlanError, build_migration_plan
from rebuild.migration.semantic import SemanticParityError, compare_semantic_manifests


def _table(cells: list[str]) -> str:
    body = "".join(f"<td>{cell}</td>" for cell in cells)
    return f'<table width="643" height="18"><tr>{body}</tr></table>'


def _transaction_row(mmdd: str, description: str, amount: str) -> str:
    return _table(["", mmdd, mmdd, description, amount, "4529", "CN", amount])


def _email(html: str, email_date: str) -> bytes:
    message = EmailMessage()
    message["Date"] = email_date
    message["Subject"] = "CMB statement"
    message.set_content("fallback")
    message.add_alternative(html, subtype="html", charset="utf-8")
    return message.as_bytes()


def _json_line(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


class _LegacyFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.emails = root / "emails"
        self.mappings = root / "mappings"
        self.reports = root / "reports"
        self.emails.mkdir(parents=True)
        self.mappings.mkdir(parents=True)
        self.reports.mkdir(parents=True)
        self.rule_id = "schedule_salary"
        self.schedule_last_date = date(2025, 8, 5)
        self.schedule_next_date = date(2025, 9, 5)
        self.schedule_source_id = legacy_schedule_occurrence_source_id(
            self.rule_id, self.schedule_last_date
        )
        self._write_all()

    def _write_all(self) -> None:
        august_name = "2025-08-10_fixture.eml"
        september_name = "2025-09-10_fixture.eml"
        august = _email(
            _transaction_row("0715", "Mapped Shop", "100.00"),
            "Sun, 10 Aug 2025 08:00:00 +0800",
        )
        september = _email(
            _transaction_row("0812", "Mapped Shop", "-20.00")
            + _transaction_row("0813", "Other Shop", "30.00"),
            "Wed, 10 Sep 2025 08:00:00 +0800",
        )
        (self.emails / august_name).write_bytes(august)
        (self.emails / september_name).write_bytes(september)

        cmb_rows: list[dict[str, object]] = []
        self.cmb_sources: list[tuple[str, object]] = []
        for filename, raw in ((august_name, august), (september_name, september)):
            parsed = parse_cmb_email(CmbEmailEvidence(raw))
            for index, record in enumerate(parsed.records, start=1):
                old_id = legacy_cmb_source_id(filename, index)
                self.cmb_sources.append((old_id, record))
                cmb_rows.append(
                    {
                        "transaction_id": old_id,
                        "transaction_date": record.transaction_date.isoformat(),
                        "amount": format(record.amount, "f"),
                        "description": record.description,
                        "source_email": filename,
                        "source_index": index,
                    }
                )
        with (self.root / "transactions.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=(
                    "transaction_id",
                    "transaction_date",
                    "amount",
                    "description",
                    "source_email",
                    "source_index",
                ),
            )
            writer.writeheader()
            writer.writerows(cmb_rows)

        manual_rows = (
            {
                "id": "manual_support",
                "type": "expense",
                "date": "2025-08-13",
                "amount": "30.00",
                "currency": "CNY",
                "description": "Other Shop manual",
                "merchant": "Other Merchant",
                "category": "餐饮美食",
                "note": "manual note",
            },
            {
                "id": "manual_income",
                "type": "income",
                "date": "2025-08-20",
                "amount": "1000.00",
                "currency": "CNY",
                "description": "工资",
                "note": "salary note",
            },
            {
                "id": self.schedule_source_id,
                "type": "income",
                "date": self.schedule_last_date.isoformat(),
                "amount": "200.00",
                "currency": "CNY",
                "description": "定期收入",
                "note": "auto note",
            },
        )
        (self.root / "manual_source_records.jsonl").write_text(
            "".join(_json_line(row) for row in manual_rows), encoding="utf-8"
        )

        purchase_source = self.cmb_sources[0][0]
        refund_source = self.cmb_sources[1][0]
        other_source = self.cmb_sources[2][0]
        self.transaction_ids = {
            "purchase": "txn_purchase_fixture",
            "refund": "txn_refund_fixture",
            "other": "txn_other_fixture",
            "income": "txn_income_fixture",
            "schedule": "txn_schedule_fixture",
        }
        links = (
            (self.transaction_ids["purchase"], purchase_source, "authoritative"),
            (self.transaction_ids["refund"], refund_source, "authoritative"),
            (self.transaction_ids["other"], other_source, "authoritative"),
            (self.transaction_ids["other"], "manual_support", "supporting"),
            (self.transaction_ids["income"], "manual_income", "authoritative"),
            (self.transaction_ids["schedule"], self.schedule_source_id, "authoritative"),
        )
        (self.root / "transaction_source_links.jsonl").write_text(
            "".join(
                _json_line(
                    {
                        "transaction_id": transaction_id,
                        "source_record_id": source_id,
                        "role": role,
                    }
                )
                for transaction_id, source_id, role in links
            ),
            encoding="utf-8",
        )

        merchants = {
            "Mapped Merchant": ["Mapped Shop"],
            "Other Merchant": ["Other Shop"],
        }
        categories = {
            "日常采购": ["Mapped Merchant"],
            "综合购物": ["Other Merchant"],
            "餐饮美食": ["餐饮占位"],
        }
        # MappingCatalog requires every categorized merchant to own a description.
        merchants["餐饮占位"] = ["unused dining description"]
        (self.mappings / "merchants.yaml").write_text(
            yaml.safe_dump(merchants, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        (self.mappings / "categories.yaml").write_text(
            yaml.safe_dump(categories, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        self.mapping_catalog = MappingCatalog(
            {
                "Mapped Shop": "Mapped Merchant",
                "Other Shop": "Other Merchant",
                "unused dining description": "餐饮占位",
            },
            {
                "Mapped Merchant": "日常采购",
                "Other Merchant": "综合购物",
                "餐饮占位": "餐饮美食",
            },
            frozenset({"日常采购", "综合购物", "餐饮美食"}),
        )

        states = (
            {
                "transaction_id": self.transaction_ids["purchase"],
                "merchant_name": "Mapped Merchant",
                "default_category": "日常采购",
                "category": "日常采购",
                "category_source": "merchant_default",
                "note": None,
            },
            {
                "transaction_id": self.transaction_ids["refund"],
                "merchant_name": "Mapped Merchant",
                "default_category": "日常采购",
                "category": "日常采购",
                "category_source": "merchant_default",
                "note": None,
            },
            {
                "transaction_id": self.transaction_ids["other"],
                "merchant_name": "Other Merchant",
                "default_category": "综合购物",
                "category": "餐饮美食",
                "category_source": "manual_override",
                "note": "manual note",
            },
            {
                "transaction_id": self.transaction_ids["income"],
                "merchant_name": None,
                "default_category": None,
                "category": "其他收入",
                "category_source": "income_default",
                "note": "salary note",
            },
            {
                "transaction_id": self.transaction_ids["schedule"],
                "merchant_name": None,
                "default_category": None,
                "category": "其他收入",
                "category_source": "income_default",
                "note": "auto note",
            },
        )
        (self.root / "enrichment_state.jsonl").write_text(
            "".join(_json_line(row) for row in states), encoding="utf-8"
        )

        schedule = [
            {
                "id": self.rule_id,
                "enabled": True,
                "type": "income",
                "amount": "200.00",
                "currency": "CNY",
                "description": "定期收入",
                "note": "auto note",
                "next_date": self.schedule_next_date.isoformat(),
                "last_occurrence_date": self.schedule_last_date.isoformat(),
                "last_source_record_id": self.schedule_source_id,
                "last_transaction_id": self.transaction_ids["schedule"],
                "last_action": "created",
            }
        ]
        (self.root / "scheduled_input_rules.json").write_text(
            json.dumps(schedule, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        feedback = {
            "id": "feedback_fixture",
            "created_at": "2025-09-11T00:00:00.000000Z",
            "status": "resolved",
            "content": "fixture feedback",
            "context": {"runtime": "desktop_web", "page": "overview"},
        }
        (self.root / "feedback.jsonl").write_text(_json_line(feedback), encoding="utf-8")
        self._write_reference_projections()

    def _write_reference_projections(self) -> None:
        cmb_records = tuple(record for _, record in self.cmb_sources)
        manual = (
            ManualEvidence(
                "manual_support",
                "expense",
                date(2025, 8, 13),
                Decimal("30.00"),
                "CNY",
                "Other Shop manual",
            ),
            ManualEvidence(
                "manual_income",
                "income",
                date(2025, 8, 20),
                Decimal("1000.00"),
                "CNY",
                "工资",
            ),
            ManualEvidence(
                self.schedule_source_id,
                "income",
                self.schedule_last_date,
                Decimal("200.00"),
                "CNY",
                "定期收入",
            ),
        )
        manual_sources = tuple(manual_evidence_to_source_record(item) for item in manual)
        old_to_new = {old_id: record.id for old_id, record in self.cmb_sources}
        old_to_new.update(
            {
                "manual_support": manual_sources[0].id,
                "manual_income": manual_sources[1].id,
                self.schedule_source_id: manual_sources[2].id,
            }
        )
        links_raw = [
            json.loads(line)
            for line in (self.root / "transaction_source_links.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        links = tuple(
            SourceLink(
                item["transaction_id"],
                old_to_new[item["source_record_id"]],
                item["role"],
            )
            for item in links_raw
        )
        source_records = cmb_records + manual_sources
        transactions = rebuild_transactions_from_source_links(source_records, links)
        records_by_id = {item.id: item for item in source_records}
        authoritative = MappingProxyType(
            {
                link.transaction_id: records_by_id[link.source_record_id]
                for link in links
                if link.role == "authoritative"
            }
        )
        decisions = (
            EnrichmentDecision(
                self.transaction_ids["other"],
                category_override="餐饮美食",
                note="manual note",
            ),
            EnrichmentDecision(self.transaction_ids["income"], note="salary note"),
            EnrichmentDecision(self.transaction_ids["schedule"], note="auto note"),
        )
        enrichments = resolve_enrichments(
            transactions, authoritative, self.mapping_catalog, decisions
        )
        enrichment_by_id = MappingProxyType(
            {item.transaction_id: item for item in enrichments}
        )
        statement_dates = frozenset(
            parse_cmb_email(CmbEmailEvidence(path.read_bytes())).statement_date
            for path in sorted(self.emails.glob("*.eml"))
        )
        spending = build_spending_projection(
            transactions, authoritative, enrichment_by_id, statement_dates
        )
        financial = build_financial_projection(
            transactions, spending.statistics, statement_dates
        )
        (self.reports / "spending_statistics.json").write_text(
            json.dumps(spending.payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.reports / "financial_summary.json").write_text(
            json.dumps(financial.payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class LegacyToCanonicalMigrationTests(unittest.TestCase):
    def test_plan_preserves_durable_identity_and_strips_materialized_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _LegacyFixture(Path(temp_dir) / "legacy")
            plan = build_migration_plan(fixture.root)

            self.assertEqual(len(plan.cmb_evidence), 2)
            self.assertEqual(len(plan.cmb_source_records), 3)
            self.assertEqual(len(plan.manual_evidence), 3)
            self.assertEqual(len(plan.transactions), 5)
            self.assertEqual(
                tuple(item.id for item in plan.transactions),
                tuple(fixture.transaction_ids.values()),
            )
            self.assertEqual(len(plan.source_links), 6)
            self.assertEqual(len(plan.enrichment_decisions), 3)
            decision_by_id = {item.transaction_id: item for item in plan.enrichment_decisions}
            self.assertNotIn(fixture.transaction_ids["purchase"], decision_by_id)
            self.assertEqual(
                decision_by_id[fixture.transaction_ids["other"]].category_override,
                "餐饮美食",
            )
            self.assertEqual(
                decision_by_id[fixture.transaction_ids["income"]].note,
                "salary note",
            )
            cmb_audit = next(
                item for item in plan.audit.source_identities if item.source_type == "cmb_email"
            )
            self.assertNotEqual(
                cmb_audit.legacy_source_record_id,
                cmb_audit.canonical_source_record_id,
            )

    def test_schedule_migrates_mixed_rule_state_into_split_canonical_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _LegacyFixture(Path(temp_dir) / "legacy")
            plan = build_migration_plan(fixture.root)

            self.assertEqual(len(plan.scheduled_rules), 1)
            self.assertEqual(plan.scheduled_rules[0].first_occurrence_date, fixture.schedule_next_date)
            execution = plan.schedule_execution[0]
            self.assertEqual(execution.last_processed_occurrence_date, fixture.schedule_last_date)
            self.assertEqual(execution.last_transaction_id, fixture.transaction_ids["schedule"])
            self.assertEqual(execution.last_action, "created")
            self.assertNotEqual(execution.last_source_record_id, fixture.schedule_source_id)

    def test_execute_publishes_only_after_restart_and_identity_reuse_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            fixture = _LegacyFixture(base / "legacy")
            target = base / "canonical"
            audit = base / "migration-audit.json"
            plan = build_migration_plan(fixture.root)

            result = execute_migration(plan, target, audit_output=audit)

            self.assertTrue(target.is_dir())
            self.assertTrue(audit.is_file())
            self.assertEqual(result.reused_source_record_count, 6)
            layout = StorageLayout(target.resolve())
            self.assertEqual(
                len(FilesystemEnrichmentDecisionStore(layout).load()), 3
            )
            schedule = FilesystemScheduleStore(layout)
            self.assertEqual(len(schedule.load_rules()), 1)
            self.assertEqual(len(schedule.load_execution()), 1)
            components = compose_runtime(
                AppConfig(
                    storage=StorageConfig(target.resolve()),
                    sources=SourceConfig(
                        cmb_email=CmbEmailSourceConfig(enabled=False)
                    ),
                )
            )
            self.assertEqual(
                tuple(item.id for item in components.runtime.current_state().household.transactions),
                tuple(fixture.transaction_ids.values()),
            )
            with self.assertRaisesRegex(MigrationExecutionError, "target already exists"):
                execute_migration(plan, target)

    def test_materialization_failure_leaves_target_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            fixture = _LegacyFixture(base / "legacy")
            target = base / "canonical"
            plan = build_migration_plan(fixture.root)
            with patch(
                "rebuild.migration.execute._validate_materialized",
                side_effect=RuntimeError("injected validation failure"),
            ):
                with self.assertRaisesRegex(MigrationExecutionError, "injected validation failure"):
                    execute_migration(plan, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(base.glob(".canonical.migration-*")), [])

    def test_csv_fact_drift_fails_before_any_target_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _LegacyFixture(Path(temp_dir) / "legacy")
            csv_path = fixture.root / "transactions.csv"
            text = csv_path.read_text(encoding="utf-8-sig")
            csv_path.write_text(text.replace("100.00", "101.00", 1), encoding="utf-8-sig")
            with self.assertRaisesRegex(MigrationPlanError, "CSV facts disagree"):
                build_migration_plan(fixture.root)

    def test_explicit_or_stale_merchant_clear_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _LegacyFixture(Path(temp_dir) / "legacy")
            path = fixture.root / "enrichment_state.jsonl"
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            rows[0].update(
                merchant_name=None,
                default_category=None,
                category="待分类",
                category_source="unclassified",
            )
            path.write_text("".join(_json_line(row) for row in rows), encoding="utf-8")
            fixture._write_reference_projections()
            with self.assertRaisesRegex(MigrationPlanError, "clears a mapped Merchant"):
                build_migration_plan(fixture.root)

    def test_projection_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = _LegacyFixture(Path(temp_dir) / "legacy")
            path = fixture.reports / "financial_summary.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["summary"]["all_data"]["total_income_minor"] += 1
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(MigrationPlanError, "Financial projection semantic parity failed"):
                build_migration_plan(fixture.root)

    def test_semantic_compare_reports_path_without_household_values(self) -> None:
        with self.assertRaisesRegex(SemanticParityError, r"\$\.transactions\[0\]\.amount") as caught:
            compare_semantic_manifests(
                {"transactions": [{"amount": "PRIVATE-A"}]},
                {"transactions": [{"amount": "PRIVATE-B"}]},
            )
        self.assertNotIn("PRIVATE", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
