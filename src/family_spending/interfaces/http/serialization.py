from __future__ import annotations

from datetime import timezone
from typing import Any

from family_spending.application.models import (
    ManualInputDeletionView,
    ManualInputRecordView,
    ManualInputView,
    MappingReviewPreview,
    MappingReviewWorkspaceView,
    ScheduledInputRuleView,
    ScheduledInputRunResult,
    TransactionView,
)
from family_spending.domain.feedback import FeedbackItem


def transaction_payload(view: TransactionView) -> dict[str, Any]:
    """Serialize the stable frontend Transaction DTO without exposing Application internals."""
    return view.to_dict()


def manual_input_payload(view: ManualInputView) -> dict[str, Any]:
    """Expose the permanent Manual evidence id through the existing public source-record handle."""
    return {
        "source_record_id": view.evidence_id,
        "action": view.action,
        "transaction": transaction_payload(view.transaction),
    }


def manual_input_record_payload(view: ManualInputRecordView) -> dict[str, Any]:
    """Preserve the current strict Manual Input API while Note stays Transaction enrichment."""
    source = view.source_record
    return {
        "source_record_id": view.evidence_id,
        "transaction_id": view.transaction_id,
        "source_role": view.source_role,
        "type": source.transaction_type,
        "date": source.transaction_date.isoformat(),
        "amount": format(source.amount, "f"),
        "currency": source.currency,
        "description": source.description,
        "note": view.transaction.enrichment.note,
        "transaction": transaction_payload(view.transaction),
    }


def manual_input_deletion_payload(view: ManualInputDeletionView) -> dict[str, Any]:
    return {
        "source_record_id": view.evidence_id,
        "transaction_id": view.transaction_id,
        "transaction_removed": view.transaction_removed,
    }


def mapping_review_workspace_payload(view: MappingReviewWorkspaceView) -> dict[str, Any]:
    return {
        "items": [
            {
                "description": item.description,
                "transaction_count": item.transaction_count,
                "total_amount": format(item.total_amount, "f"),
                "currency": item.currency,
                "latest_date": item.latest_date.isoformat(),
                "source_types": list(item.source_types),
                "transaction_only_exception_count": item.transaction_only_exception_count,
            }
            for item in view.items
        ],
        "merchants": [
            {"name": item.name, "default_category": item.default_category}
            for item in view.merchants
        ],
        "categories": list(view.categories),
    }


def mapping_review_preview_payload(view: MappingReviewPreview) -> dict[str, Any]:
    return {
        "token": view.token,
        "description": view.description,
        "merchant": view.merchant,
        "category": view.category,
        "is_new_merchant": view.is_new_merchant,
        "previous_default_category": view.previous_default_category,
        "description_transaction_count": view.description_transaction_count,
        "description_affected_transaction_count": view.description_affected_transaction_count,
        "default_category_affected_transaction_count": (
            view.default_category_affected_transaction_count
        ),
        "total_affected_transaction_count": view.total_affected_transaction_count,
        "preserved_merchant_exception_count": view.preserved_merchant_exception_count,
        "preserved_category_exception_count": view.preserved_category_exception_count,
    }


def feedback_payload(item: FeedbackItem) -> dict[str, Any]:
    """Serialize UTC Feedback with absent context fields omitted exactly as the public contract expects."""
    context = {
        key: value
        for key, value in {
            "runtime": item.context.runtime,
            "page": item.context.page,
            "workspace": item.context.workspace,
            "entity_type": item.context.entity_type,
            "entity_id": item.context.entity_id,
        }.items()
        if value is not None
    }
    created_at = item.created_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if created_at.endswith("+00:00"):
        created_at = created_at[:-6] + "Z"
    return {
        "id": item.id,
        "created_at": created_at,
        "status": item.status,
        "content": item.content,
        "context": context,
    }


def scheduled_rule_payload(view: ScheduledInputRuleView) -> dict[str, Any]:
    """Merge canonical rule/config and execution state into the existing strict client DTO."""
    return {
        "id": view.id,
        "enabled": view.enabled,
        "type": view.transaction_type,
        "amount": format(view.amount, "f"),
        "currency": view.currency,
        "description": view.description,
        "note": view.note,
        "next_date": view.next_date.isoformat(),
        "last_occurrence_date": (
            view.last_occurrence_date.isoformat()
            if view.last_occurrence_date is not None
            else None
        ),
        "last_source_record_id": view.last_source_record_id,
        "last_transaction_id": view.last_transaction_id,
        "last_action": view.last_action,
    }


def scheduled_run_payload(result: ScheduledInputRunResult) -> dict[str, Any]:
    return {
        "generated_count": len(result.occurrences),
        "occurrences": [
            {
                "rule_id": item.rule_id,
                "occurrence_date": item.occurrence_date.isoformat(),
                "source_record_id": item.source_record_id,
                "transaction_id": item.transaction_id,
                "action": item.action,
            }
            for item in result.occurrences
        ],
    }
