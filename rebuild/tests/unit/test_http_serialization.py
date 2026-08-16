from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from family_spending.application.models import (
    ManualInputDeletionView,
    ManualInputRecordView,
    ManualInputView,
    MappingReviewItem,
    MappingReviewPreview,
    MappingReviewWorkspaceView,
    MerchantMappingOption,
    ScheduledInputOccurrence,
    ScheduledInputRuleView,
    ScheduledInputRunResult,
    TransactionView,
)
from family_spending.domain.enrichment import ResolvedEnrichment
from family_spending.domain.feedback import FeedbackContext, FeedbackItem
from family_spending.domain.source import SourceIdentity, SourceRecord
from family_spending.domain.transaction import Transaction
from family_spending.interfaces.http.serialization import (
    feedback_payload,
    manual_input_deletion_payload,
    manual_input_payload,
    manual_input_record_payload,
    mapping_review_preview_payload,
    mapping_review_workspace_payload,
    scheduled_rule_payload,
    scheduled_run_payload,
    transaction_payload,
)


def _transaction_view() -> TransactionView:
    source = SourceRecord(
        identity=SourceIdentity("manual", "manual_1", "record"),
        transaction_type="expense",
        transaction_date=date(2026, 8, 16),
        amount=Decimal("12.50"),
        currency="CNY",
        description="lunch",
    )
    transaction = Transaction(
        id="txn_1",
        transaction_type="expense",
        transaction_date=source.transaction_date,
        amount=source.amount,
        currency=source.currency,
    )
    enrichment = ResolvedEnrichment(
        transaction_id=transaction.id,
        merchant_name="Lunch",
        display_name="Lunch",
        default_category="餐饮美食",
        category="餐饮美食",
        category_source="merchant_default",
        is_unclassified=False,
        review_signals=(),
        note="note",
    )
    return TransactionView(transaction, source, enrichment, ())


class HttpSerializationTests(unittest.TestCase):
    def test_transaction_and_manual_dtos_match_strict_frontend_keys(self) -> None:
        transaction = _transaction_view()
        self.assertEqual(
            set(transaction_payload(transaction)),
            {"id", "type", "date", "amount", "currency", "source", "enrichment"},
        )
        manual = ManualInputView(
            evidence_id="manual_1",
            source_record_id=transaction.source_record.id,
            action="created",
            transaction=transaction,
        )
        self.assertEqual(
            manual_input_payload(manual),
            {
                "source_record_id": "manual_1",
                "action": "created",
                "transaction": transaction_payload(transaction),
            },
        )
        record = ManualInputRecordView(
            evidence_id="manual_1",
            source_record=transaction.source_record,
            transaction_id="txn_1",
            source_role="authoritative",
            transaction=transaction,
        )
        payload = manual_input_record_payload(record)
        self.assertEqual(
            set(payload),
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
        self.assertEqual(payload["source_record_id"], "manual_1")
        self.assertEqual(payload["note"], "note")
        deletion = ManualInputDeletionView(
            evidence_id="manual_1",
            source_record_id=transaction.source_record.id,
            transaction_id="txn_1",
            transaction_removed=True,
        )
        self.assertEqual(
            manual_input_deletion_payload(deletion),
            {
                "source_record_id": "manual_1",
                "transaction_id": "txn_1",
                "transaction_removed": True,
            },
        )

    def test_mapping_feedback_and_schedule_dtos_are_transport_only_views(self) -> None:
        workspace = MappingReviewWorkspaceView(
            items=(
                MappingReviewItem(
                    "unknown",
                    2,
                    Decimal("30"),
                    "CNY",
                    date(2026, 8, 16),
                    ("manual",),
                    1,
                ),
            ),
            merchants=(MerchantMappingOption("Known", "餐饮美食"),),
            categories=("餐饮美食",),
        )
        self.assertEqual(
            set(mapping_review_workspace_payload(workspace)),
            {"items", "merchants", "categories"},
        )
        preview = MappingReviewPreview(
            "a" * 64,
            "unknown",
            "Known",
            "餐饮美食",
            False,
            "餐饮美食",
            2,
            2,
            0,
            2,
            0,
            1,
        )
        self.assertEqual(mapping_review_preview_payload(preview)["token"], "a" * 64)

        feedback = FeedbackItem(
            id="feedback_1",
            created_at=datetime(2026, 8, 16, 1, 2, 3, tzinfo=timezone.utc),
            status="open",
            content="hello",
            context=FeedbackContext(runtime="desktop_web", page="overview"),
        )
        feedback_json = feedback_payload(feedback)
        self.assertTrue(feedback_json["created_at"].endswith("Z"))
        self.assertEqual(feedback_json["context"], {"runtime": "desktop_web", "page": "overview"})

        rule = ScheduledInputRuleView(
            id="schedule_1",
            enabled=True,
            transaction_type="income",
            amount=Decimal("15000"),
            currency="CNY",
            description="salary",
            note=None,
            next_date=date(2026, 9, 6),
            last_occurrence_date=date(2026, 8, 6),
            last_source_record_id="src_1",
            last_transaction_id="txn_1",
            last_action="recovered",
        )
        rule_json = scheduled_rule_payload(rule)
        self.assertEqual(
            set(rule_json),
            {
                "id",
                "enabled",
                "type",
                "amount",
                "currency",
                "description",
                "note",
                "next_date",
                "last_occurrence_date",
                "last_source_record_id",
                "last_transaction_id",
                "last_action",
            },
        )
        occurrence = ScheduledInputOccurrence(
            "schedule_1",
            date(2026, 8, 6),
            "schedule_occ_1",
            "src_1",
            "txn_1",
            "recovered",
        )
        run_json = scheduled_run_payload(ScheduledInputRunResult((occurrence,)))
        self.assertEqual(run_json["generated_count"], 1)
        self.assertNotIn("evidence_id", run_json["occurrences"][0])


if __name__ == "__main__":
    unittest.main()
