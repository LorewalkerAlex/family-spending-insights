from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from family_spending.domain.enrichment import (
    INCOME_DEFAULT_CATEGORY,
    EnrichmentDecision,
    ResolvedEnrichment,
    resolve_enrichment,
    resolve_enrichments,
)
from family_spending.domain.feedback import FeedbackItem
from family_spending.domain.mapping import MappingCatalog, UNCLASSIFIED_CATEGORY
from family_spending.domain.scheduling import ScheduleExecutionState, ScheduledRule
from family_spending.domain.source import SourceRecord
from family_spending.domain.transaction import (
    SourceLink,
    Transaction,
    rebuild_transactions_from_source_links,
)
from family_spending.projections.financial import build_financial_projection
from family_spending.projections.spending import build_spending_projection
from family_spending.sources.cmb_email.evidence import CmbEmailEvidence
from family_spending.sources.cmb_email.parser import parse_cmb_email
from family_spending.sources.manual.model import ManualEvidence, manual_evidence_to_source_record
from rebuild.migration.legacy import (
    LegacyCmbTransaction,
    LegacyEnrichmentState,
    LegacyLayout,
    LegacyManualRecord,
    LegacyScheduledRule,
    LegacySnapshotError,
    legacy_cmb_source_id,
    legacy_schedule_occurrence_source_id,
    load_legacy_cmb_transactions,
    load_legacy_enrichment,
    load_legacy_feedback,
    load_legacy_manual_records,
    load_legacy_mappings,
    load_legacy_schedules,
    load_legacy_source_links,
    load_projection,
)


class MigrationPlanError(RuntimeError):
    """Raised when legacy state cannot be mapped to canonical state without ambiguity."""


@dataclass(frozen=True)
class SourceIdentityMigration:
    """Audit one legacy SourceRecord identity rewritten to canonical evidence identity."""

    source_type: str
    legacy_source_record_id: str
    canonical_source_record_id: str
    evidence_identity: str
    record_locator: str


@dataclass(frozen=True)
class MigrationAudit:
    """Private audit metadata proving the complete source and durable-state migration."""

    source_identities: tuple[SourceIdentityMigration, ...]
    cmb_evidence_count: int
    cmb_source_record_count: int
    manual_evidence_count: int
    transaction_count: int
    source_link_count: int
    enrichment_decision_count: int
    scheduled_rule_count: int
    schedule_execution_count: int
    feedback_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "counts": {
                "cmb_evidence": self.cmb_evidence_count,
                "cmb_source_records": self.cmb_source_record_count,
                "manual_evidence": self.manual_evidence_count,
                "transactions": self.transaction_count,
                "source_links": self.source_link_count,
                "enrichment_decisions": self.enrichment_decision_count,
                "scheduled_rules": self.scheduled_rule_count,
                "schedule_execution": self.schedule_execution_count,
                "feedback": self.feedback_count,
            },
            "source_identity_map": [
                {
                    "source_type": item.source_type,
                    "legacy_source_record_id": item.legacy_source_record_id,
                    "canonical_source_record_id": item.canonical_source_record_id,
                    "evidence_identity": item.evidence_identity,
                    "record_locator": item.record_locator,
                }
                for item in self.source_identities
            ],
        }


@dataclass(frozen=True)
class MigrationPlan:
    """Fully validated canonical state ready for atomic sandbox materialization."""

    legacy_root: Path
    cmb_evidence: tuple[CmbEmailEvidence, ...]
    cmb_source_records: tuple[SourceRecord, ...]
    manual_evidence: tuple[ManualEvidence, ...]
    manual_source_records: tuple[SourceRecord, ...]
    source_links: tuple[SourceLink, ...]
    transactions: tuple[Transaction, ...]
    mappings: MappingCatalog
    enrichment_decisions: tuple[EnrichmentDecision, ...]
    resolved_enrichments: tuple[ResolvedEnrichment, ...]
    scheduled_rules: tuple[ScheduledRule, ...]
    schedule_execution: tuple[ScheduleExecutionState, ...]
    feedback: tuple[FeedbackItem, ...]
    statement_dates: frozenset[date]
    spending_payload: dict[str, object]
    financial_payload: dict[str, object]
    audit: MigrationAudit

    @property
    def source_records(self) -> tuple[SourceRecord, ...]:
        return self.cmb_source_records + self.manual_source_records


@dataclass(frozen=True)
class _CmbMigration:
    evidence: tuple[CmbEmailEvidence, ...]
    source_records: tuple[SourceRecord, ...]
    source_map: Mapping[str, SourceRecord]
    audits: tuple[SourceIdentityMigration, ...]
    statement_dates: frozenset[date]


@dataclass(frozen=True)
class _ManualMigration:
    evidence: tuple[ManualEvidence, ...]
    source_records: tuple[SourceRecord, ...]
    source_map: Mapping[str, SourceRecord]
    audits: tuple[SourceIdentityMigration, ...]


def _same_cmb_facts(legacy: LegacyCmbTransaction, canonical: SourceRecord) -> bool:
    return (
        canonical.transaction_type == "expense"
        and canonical.transaction_date == legacy.transaction_date
        and canonical.amount == legacy.amount
        and canonical.currency == "CNY"
        and canonical.description == legacy.description
    )


def _migrate_cmb(layout: LegacyLayout) -> _CmbMigration:
    legacy_rows = load_legacy_cmb_transactions(layout.transactions)
    rows_by_id = {row.source_record_id: row for row in legacy_rows}
    if len(rows_by_id) != len(legacy_rows):
        raise MigrationPlanError("Legacy CMB transaction audit contains duplicate ids")
    if not layout.emails.is_dir():
        raise MigrationPlanError(f"Legacy CMB evidence directory is missing: {layout.emails}")

    evidence_items: list[CmbEmailEvidence] = []
    records: list[SourceRecord] = []
    source_map: dict[str, SourceRecord] = {}
    audits: list[SourceIdentityMigration] = []
    statement_dates: set[date] = set()
    matched_legacy_ids: set[str] = set()
    email_paths = sorted(layout.emails.glob("*.eml"), key=lambda item: item.name)
    if not email_paths and legacy_rows:
        raise MigrationPlanError("Legacy CMB CSV has rows but no EML evidence exists")

    for path in email_paths:
        try:
            evidence = CmbEmailEvidence(path.read_bytes())
            parsed = parse_cmb_email(evidence)
        except Exception as exc:
            raise MigrationPlanError(f"Unable to parse legacy CMB evidence {path}: {exc}") from exc
        evidence_items.append(evidence)
        statement_dates.add(parsed.statement_date)
        for source_index, record in enumerate(parsed.records, start=1):
            legacy_id = legacy_cmb_source_id(path.name, source_index)
            legacy_row = rows_by_id.get(legacy_id)
            if legacy_row is None:
                raise MigrationPlanError(
                    f"Parsed CMB evidence has no matching legacy CSV row: {path.name} index {source_index}"
                )
            if (
                legacy_row.source_email != path.name
                or legacy_row.source_index != source_index
                or not _same_cmb_facts(legacy_row, record)
            ):
                raise MigrationPlanError(
                    f"Legacy CMB CSV facts disagree with raw evidence: {path.name} index {source_index}"
                )
            if legacy_id in source_map:
                raise MigrationPlanError(f"Legacy CMB source id maps more than once: {legacy_id!r}")
            source_map[legacy_id] = record
            matched_legacy_ids.add(legacy_id)
            records.append(record)
            audits.append(
                SourceIdentityMigration(
                    source_type="cmb_email",
                    legacy_source_record_id=legacy_id,
                    canonical_source_record_id=record.id,
                    evidence_identity=record.identity.evidence_identity,
                    record_locator=record.identity.record_locator,
                )
            )

    unmatched = sorted(set(rows_by_id) - matched_legacy_ids)
    if unmatched:
        raise MigrationPlanError(
            f"Legacy CMB CSV rows could not be correlated to raw EML evidence: count={len(unmatched)}"
        )
    canonical_ids = [record.id for record in records]
    if len(canonical_ids) != len(set(canonical_ids)):
        raise MigrationPlanError("Canonical CMB SourceRecord identities are not unique")
    return _CmbMigration(
        evidence=tuple(evidence_items),
        source_records=tuple(records),
        source_map=MappingProxyType(source_map),
        audits=tuple(audits),
        statement_dates=frozenset(statement_dates),
    )


def _migrate_manual(records: tuple[LegacyManualRecord, ...]) -> _ManualMigration:
    evidence_items: list[ManualEvidence] = []
    source_records: list[SourceRecord] = []
    source_map: dict[str, SourceRecord] = {}
    audits: list[SourceIdentityMigration] = []
    for item in records:
        try:
            evidence = ManualEvidence(
                evidence_id=item.source_record_id,
                transaction_type=item.transaction_type,
                transaction_date=item.transaction_date,
                amount=item.amount,
                currency=item.currency,
                description=item.description,
            )
            source = manual_evidence_to_source_record(evidence)
        except Exception as exc:
            raise MigrationPlanError(
                f"Legacy Manual Source {item.source_record_id!r} cannot become canonical evidence: {exc}"
            ) from exc
        if item.source_record_id in source_map:
            raise MigrationPlanError(f"Legacy Manual source id maps more than once: {item.source_record_id!r}")
        evidence_items.append(evidence)
        source_records.append(source)
        source_map[item.source_record_id] = source
        audits.append(
            SourceIdentityMigration(
                source_type="manual",
                legacy_source_record_id=item.source_record_id,
                canonical_source_record_id=source.id,
                evidence_identity=source.identity.evidence_identity,
                record_locator=source.identity.record_locator,
            )
        )
    return _ManualMigration(
        evidence=tuple(evidence_items),
        source_records=tuple(source_records),
        source_map=MappingProxyType(source_map),
        audits=tuple(audits),
    )


def _rewrite_links(
    legacy_links,
    source_map: Mapping[str, SourceRecord],
) -> tuple[SourceLink, ...]:
    links: list[SourceLink] = []
    for legacy in legacy_links:
        record = source_map.get(legacy.source_record_id)
        if record is None:
            raise MigrationPlanError(
                f"Legacy SourceLink references unmapped source {legacy.source_record_id!r}"
            )
        try:
            links.append(
                SourceLink(
                    transaction_id=legacy.transaction_id,
                    source_record_id=record.id,
                    role=legacy.role,
                )
            )
        except Exception as exc:
            raise MigrationPlanError(f"Invalid migrated SourceLink: {exc}") from exc
    return tuple(links)


def _authoritative_sources(
    source_records: tuple[SourceRecord, ...],
    links: tuple[SourceLink, ...],
) -> Mapping[str, SourceRecord]:
    records = {item.id: item for item in source_records}
    authoritative: dict[str, SourceRecord] = {}
    for link in links:
        if link.role != "authoritative":
            continue
        if link.transaction_id in authoritative:
            raise MigrationPlanError(
                f"Migrated Transaction {link.transaction_id!r} has multiple authoritative sources"
            )
        try:
            authoritative[link.transaction_id] = records[link.source_record_id]
        except KeyError as exc:
            raise MigrationPlanError(
                f"Migrated SourceLink references missing canonical source {link.source_record_id!r}"
            ) from exc
    return MappingProxyType(authoritative)


def _expected_legacy_default(mappings: MappingCatalog, merchant: str | None) -> str | None:
    return mappings.default_category_for_merchant(merchant)


def _decision_for_state(
    transaction: Transaction,
    source: SourceRecord,
    state: LegacyEnrichmentState,
    mappings: MappingCatalog,
) -> EnrichmentDecision | None:
    if state.transaction_id != transaction.id:
        raise MigrationPlanError("Legacy Enrichment state joined to wrong Transaction")

    if transaction.transaction_type == "income":
        if (
            state.merchant_name is not None
            or state.default_category is not None
            or state.category != INCOME_DEFAULT_CATEGORY
            or state.category_source != "income_default"
        ):
            raise MigrationPlanError(
                f"Income Transaction {transaction.id!r} has non-canonicalizable legacy Enrichment"
            )
        if state.note is None:
            return None
        return EnrichmentDecision(transaction_id=transaction.id, note=state.note)

    mapped_merchant = mappings.merchant_for_description(source.description)
    expected_default = _expected_legacy_default(mappings, state.merchant_name)
    if state.default_category != expected_default:
        raise MigrationPlanError(
            f"Legacy Enrichment default Category is stale for Transaction {transaction.id!r}"
        )

    if state.merchant_name == mapped_merchant:
        merchant_override = None
    elif state.merchant_name is None and mapped_merchant is not None:
        raise MigrationPlanError(
            f"Legacy Transaction {transaction.id!r} explicitly/stalely clears a mapped Merchant; "
            "canonical sparse decisions cannot distinguish this without guessing"
        )
    else:
        merchant_override = state.merchant_name

    if state.category_source == "merchant_default":
        if (
            state.merchant_name is None
            or state.default_category is None
            or state.category != state.default_category
        ):
            raise MigrationPlanError(
                f"Invalid merchant_default Enrichment for Transaction {transaction.id!r}"
            )
        category_override = None
    elif state.category_source == "unclassified":
        if state.default_category is not None or state.category != UNCLASSIFIED_CATEGORY:
            raise MigrationPlanError(
                f"Invalid unclassified Enrichment for Transaction {transaction.id!r}"
            )
        category_override = None
    elif state.category_source in ("transaction_override", "manual_override"):
        if state.category not in mappings.categories:
            raise MigrationPlanError(
                f"Legacy Transaction {transaction.id!r} has unknown explicit Category {state.category!r}"
            )
        category_override = state.category
    elif state.category_source == "income_default":
        raise MigrationPlanError(
            f"Expense Transaction {transaction.id!r} cannot use income_default Enrichment"
        )
    else:  # pragma: no cover - strict legacy parser closes this branch
        raise MigrationPlanError(f"Unknown legacy category source {state.category_source!r}")

    if merchant_override is None and category_override is None and state.note is None:
        return None
    return EnrichmentDecision(
        transaction_id=transaction.id,
        merchant_override=merchant_override,
        category_override=category_override,
        note=state.note,
    )


def _assert_enrichment_parity(
    transaction: Transaction,
    source: SourceRecord,
    state: LegacyEnrichmentState,
    mappings: MappingCatalog,
    decision: EnrichmentDecision | None,
) -> ResolvedEnrichment:
    try:
        resolved = resolve_enrichment(transaction, source, mappings, decision)
    except Exception as exc:
        raise MigrationPlanError(
            f"Canonical Enrichment cannot resolve Transaction {transaction.id!r}: {exc}"
        ) from exc
    expected_source = (
        "transaction_override"
        if state.category_source in ("manual_override", "transaction_override")
        else state.category_source
    )
    expected_display = state.merchant_name or source.description or source.id
    actual = (
        resolved.merchant_name,
        resolved.display_name,
        resolved.default_category,
        resolved.category,
        resolved.category_source,
        resolved.note,
        resolved.is_unclassified,
    )
    expected = (
        state.merchant_name,
        expected_display,
        state.default_category,
        state.category,
        expected_source,
        state.note,
        state.category == UNCLASSIFIED_CATEGORY,
    )
    if actual != expected:
        raise MigrationPlanError(
            f"Resolved Enrichment parity failed for Transaction {transaction.id!r}"
        )
    return resolved


def _migrate_enrichment(
    transactions: tuple[Transaction, ...],
    authoritative: Mapping[str, SourceRecord],
    states: tuple[LegacyEnrichmentState, ...],
    mappings: MappingCatalog,
) -> tuple[tuple[EnrichmentDecision, ...], tuple[ResolvedEnrichment, ...]]:
    by_id = {state.transaction_id: state for state in states}
    transaction_ids = {item.id for item in transactions}
    if set(by_id) != transaction_ids:
        missing = transaction_ids - set(by_id)
        orphan = set(by_id) - transaction_ids
        raise MigrationPlanError(
            "Legacy Enrichment coverage must exactly match Transactions; "
            f"missing={len(missing)}, orphan={len(orphan)}"
        )
    decisions: list[EnrichmentDecision] = []
    resolved: list[ResolvedEnrichment] = []
    for transaction in transactions:
        source = authoritative[transaction.id]
        state = by_id[transaction.id]
        decision = _decision_for_state(transaction, source, state, mappings)
        if decision is not None:
            decisions.append(decision)
        resolved.append(
            _assert_enrichment_parity(
                transaction,
                source,
                state,
                mappings,
                decision,
            )
        )
    return tuple(decisions), tuple(resolved)


def _migrate_schedules(
    legacy_rules: tuple[LegacyScheduledRule, ...],
    manual_by_legacy_id: Mapping[str, SourceRecord],
    links: tuple[SourceLink, ...],
) -> tuple[tuple[ScheduledRule, ...], tuple[ScheduleExecutionState, ...]]:
    link_by_source = {item.source_record_id: item for item in links}
    rules: list[ScheduledRule] = []
    execution: list[ScheduleExecutionState] = []
    for legacy in legacy_rules:
        try:
            rule = ScheduledRule(
                id=legacy.id,
                enabled=legacy.enabled,
                transaction_type=legacy.transaction_type,
                amount=legacy.amount,
                description=legacy.description,
                first_occurrence_date=legacy.next_date,
                currency=legacy.currency,
                note=legacy.note,
            )
        except Exception as exc:
            raise MigrationPlanError(
                f"Legacy Scheduled Rule {legacy.id!r} cannot be represented canonically: {exc}"
            ) from exc
        rules.append(rule)
        if legacy.last_occurrence_date is None:
            continue
        assert legacy.last_source_record_id is not None
        assert legacy.last_transaction_id is not None
        assert legacy.last_action is not None
        expected_legacy_source = legacy_schedule_occurrence_source_id(
            legacy.id,
            legacy.last_occurrence_date,
        )
        if legacy.last_source_record_id != expected_legacy_source:
            raise MigrationPlanError(
                f"Legacy Scheduled Rule {legacy.id!r} has unexpected last Source identity"
            )
        canonical_source = manual_by_legacy_id.get(legacy.last_source_record_id)
        if canonical_source is None:
            raise MigrationPlanError(
                f"Legacy Scheduled Rule {legacy.id!r} last Manual occurrence is missing"
            )
        migrated_link = link_by_source.get(canonical_source.id)
        if migrated_link is None or migrated_link.transaction_id != legacy.last_transaction_id:
            raise MigrationPlanError(
                f"Legacy Scheduled Rule {legacy.id!r} last occurrence link is inconsistent"
            )
        try:
            execution.append(
                ScheduleExecutionState(
                    rule_id=legacy.id,
                    last_processed_occurrence_date=legacy.last_occurrence_date,
                    last_source_record_id=canonical_source.id,
                    last_transaction_id=legacy.last_transaction_id,
                    last_action=legacy.last_action,
                )
            )
        except Exception as exc:
            raise MigrationPlanError(
                f"Legacy Scheduled Rule {legacy.id!r} execution state is invalid: {exc}"
            ) from exc
    return tuple(rules), tuple(execution)


def build_migration_plan(legacy_root: Path | str) -> MigrationPlan:
    """Build and validate the complete migration in memory before any canonical target is written."""
    layout = LegacyLayout(Path(legacy_root))
    try:
        cmb = _migrate_cmb(layout)
        legacy_manual = load_legacy_manual_records(layout.manual_source)
        manual = _migrate_manual(legacy_manual)
        mappings = load_legacy_mappings(layout.merchants, layout.categories)
        legacy_links = load_legacy_source_links(layout.source_links)
        source_map = dict(cmb.source_map)
        for legacy_id, source in manual.source_map.items():
            if legacy_id in source_map:
                raise MigrationPlanError(f"Legacy source id collision {legacy_id!r}")
            source_map[legacy_id] = source
        links = _rewrite_links(legacy_links, source_map)
        source_records = cmb.source_records + manual.source_records
        linked_source_ids = {item.source_record_id for item in links}
        all_source_ids = {item.id for item in source_records}
        if linked_source_ids != all_source_ids:
            missing = all_source_ids - linked_source_ids
            orphan = linked_source_ids - all_source_ids
            raise MigrationPlanError(
                "Stabilized legacy snapshot must link every SourceRecord exactly once; "
                f"unlinked={len(missing)}, orphan_links={len(orphan)}"
            )
        transactions = rebuild_transactions_from_source_links(source_records, links)
        authoritative = _authoritative_sources(source_records, links)
        legacy_states = load_legacy_enrichment(layout.enrichment_state)
        decisions, resolved = _migrate_enrichment(
            transactions,
            authoritative,
            legacy_states,
            mappings,
        )
        # Re-run the batch resolver so stale or orphan sparse decisions fail the same way Runtime will.
        resolved_batch = resolve_enrichments(transactions, authoritative, mappings, decisions)
        if resolved_batch != resolved:
            raise MigrationPlanError("Batch Enrichment resolution differs from per-Transaction migration parity")
        legacy_schedules = load_legacy_schedules(layout.scheduled_rules)
        rules, execution = _migrate_schedules(legacy_schedules, manual.source_map, links)
        feedback = load_legacy_feedback(layout.feedback)
        spending = build_spending_projection(
            transactions,
            authoritative,
            MappingProxyType({item.transaction_id: item for item in resolved}),
            cmb.statement_dates,
        )
        financial = build_financial_projection(
            transactions,
            spending.statistics,
            cmb.statement_dates,
        )
        legacy_spending = load_projection(layout.spending_statistics, "spending")
        legacy_financial = load_projection(layout.financial_summary, "financial")
        if spending.payload != legacy_spending:
            raise MigrationPlanError("Spending projection semantic parity failed")
        if financial.payload != legacy_financial:
            raise MigrationPlanError("Financial projection semantic parity failed")
    except MigrationPlanError:
        raise
    except LegacySnapshotError as exc:
        raise MigrationPlanError(str(exc)) from exc
    except Exception as exc:
        raise MigrationPlanError(f"Unable to build migration plan: {exc}") from exc

    audit = MigrationAudit(
        source_identities=cmb.audits + manual.audits,
        cmb_evidence_count=len(cmb.evidence),
        cmb_source_record_count=len(cmb.source_records),
        manual_evidence_count=len(manual.evidence),
        transaction_count=len(transactions),
        source_link_count=len(links),
        enrichment_decision_count=len(decisions),
        scheduled_rule_count=len(rules),
        schedule_execution_count=len(execution),
        feedback_count=len(feedback),
    )
    return MigrationPlan(
        legacy_root=layout.data_root,
        cmb_evidence=cmb.evidence,
        cmb_source_records=cmb.source_records,
        manual_evidence=manual.evidence,
        manual_source_records=manual.source_records,
        source_links=links,
        transactions=transactions,
        mappings=mappings,
        enrichment_decisions=decisions,
        resolved_enrichments=resolved,
        scheduled_rules=rules,
        schedule_execution=execution,
        feedback=feedback,
        statement_dates=cmb.statement_dates,
        spending_payload=spending.payload,
        financial_payload=financial.payload,
        audit=audit,
    )
