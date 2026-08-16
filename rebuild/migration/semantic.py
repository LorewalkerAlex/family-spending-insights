from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from family_spending.domain.enrichment import consumption_review_signals
from family_spending.interfaces.http.serialization import feedback_payload, scheduled_rule_payload
from family_spending.projections.spending import build_spending_projection
from family_spending.runtime.composition import RuntimeComponents
from rebuild.migration.plan import MigrationPlan


class SemanticParityError(RuntimeError):
    """Raised when two private semantic manifests differ at a business-visible path."""


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def semantic_manifest_from_plan(plan: MigrationPlan) -> dict[str, object]:
    """Build a deterministic private manifest for migration/parity audit, never production runtime use."""
    authoritative = {
        link.transaction_id: link.source_record_id
        for link in plan.source_links
        if link.role == "authoritative"
    }
    source_by_id = {item.id: item for item in plan.source_records}
    enrichment_by_id = {item.transaction_id: item for item in plan.resolved_enrichments}
    projection = build_spending_projection(
        plan.transactions,
        {transaction_id: source_by_id[source_id] for transaction_id, source_id in authoritative.items()},
        enrichment_by_id,
        plan.statement_dates,
    )
    net_by_id = {item.transaction_id: item for item in projection.refund.net_consumption}
    execution_by_rule = {item.rule_id: item for item in plan.schedule_execution}
    return {
        "schema_version": 1,
        "transactions": [
            {
                "id": item.id,
                "type": item.transaction_type,
                "date": item.transaction_date.isoformat(),
                "amount": format(item.amount, "f"),
                "currency": item.currency,
                "source": {
                    "id": authoritative[item.id],
                    "type": source_by_id[authoritative[item.id]].source_type,
                    "description": source_by_id[authoritative[item.id]].description,
                },
                "enrichment": {
                    "merchant": enrichment_by_id[item.id].merchant_name,
                    "display_name": enrichment_by_id[item.id].display_name,
                    "default_category": enrichment_by_id[item.id].default_category,
                    "category": enrichment_by_id[item.id].category,
                    "category_source": enrichment_by_id[item.id].category_source,
                    "note": enrichment_by_id[item.id].note,
                    "is_unclassified": enrichment_by_id[item.id].is_unclassified,
                    "review_signals": list(
                        consumption_review_signals(
                            enrichment_by_id[item.id], net_by_id[item.id].spending
                        )
                        if item.id in net_by_id
                        else enrichment_by_id[item.id].review_signals
                    ),
                },
            }
            for item in plan.transactions
        ],
        "source_links": [
            {
                "transaction_id": item.transaction_id,
                "source_record_id": item.source_record_id,
                "role": item.role,
            }
            for item in plan.source_links
        ],
        "spending_statistics": _json_value(plan.spending_payload),
        "financial_summary": _json_value(plan.financial_payload),
        "scheduled_inputs": [
            scheduled_rule_payload(
                # Avoid importing Application service just to join migration-owned state.
                _schedule_view(rule, execution_by_rule.get(rule.id))
            )
            for rule in plan.scheduled_rules
        ],
        "feedback": [feedback_payload(item) for item in plan.feedback],
    }


def semantic_manifest_from_runtime(components: RuntimeComponents) -> dict[str, object]:
    """Export the same deterministic manifest from a materialized canonical household root."""
    app = components.application
    state = components.runtime.current_state()
    return {
        "schema_version": 1,
        "transactions": [item.to_dict() for item in app.list_transactions()],
        "source_links": [
            {
                "transaction_id": item.transaction_id,
                "source_record_id": item.source_record_id,
                "role": item.role,
            }
            for item in state.household.source_links
        ],
        "spending_statistics": _json_value(app.get_spending_statistics()),
        "financial_summary": _json_value(app.get_financial_summary()),
        "scheduled_inputs": [
            scheduled_rule_payload(item) for item in app.list_scheduled_input_views()
        ],
        "feedback": [feedback_payload(item) for item in reversed(app.list_feedback())],
    }


def _schedule_view(rule, execution):
    from family_spending.application.models import ScheduledInputRuleView
    from family_spending.domain.scheduling import next_occurrence_date

    return ScheduledInputRuleView(
        id=rule.id,
        enabled=rule.enabled,
        transaction_type=rule.transaction_type,
        amount=rule.amount,
        currency=rule.currency,
        description=rule.description,
        note=rule.note,
        next_date=next_occurrence_date(rule, execution),
        last_occurrence_date=(
            execution.last_processed_occurrence_date if execution is not None else None
        ),
        last_source_record_id=(execution.last_source_record_id if execution is not None else None),
        last_transaction_id=(execution.last_transaction_id if execution is not None else None),
        last_action=(execution.last_action if execution is not None else None),
    )


def _first_difference(left: object, right: object, path: str = "$") -> str | None:
    if type(left) is not type(right):
        return f"{path} (type)"
    if isinstance(left, dict):
        if set(left) != set(right):
            return f"{path} (keys)"
        for key in sorted(left):
            difference = _first_difference(left[key], right[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path} (length)"
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            difference = _first_difference(left_item, right_item, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if left != right:
        return path
    return None


def compare_semantic_manifests(reference: object, candidate: object) -> None:
    """Fail with a location only, avoiding private household values in terminal output."""
    difference = _first_difference(reference, candidate)
    if difference is not None:
        raise SemanticParityError(f"Semantic manifest differs at {difference}")


def read_semantic_manifest(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SemanticParityError(f"Unable to read semantic manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise SemanticParityError(f"Unsupported semantic manifest {path}")
    return raw
