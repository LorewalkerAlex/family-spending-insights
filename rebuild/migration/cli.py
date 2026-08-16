from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from rebuild.migration.execute import execute_migration
from rebuild.migration.plan import build_migration_plan
from rebuild.migration.semantic import (
    compare_semantic_manifests,
    read_semantic_manifest,
    semantic_manifest_from_plan,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="family-spending-migration")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="validate a legacy snapshot without writing canonical data")
    plan.add_argument("--legacy-root", type=Path, required=True)
    plan.add_argument("--audit-output", type=Path)
    plan.add_argument("--semantic-output", type=Path)

    migrate = subparsers.add_parser("migrate", help="atomically materialize a validated canonical sandbox")
    migrate.add_argument("--legacy-root", type=Path, required=True)
    migrate.add_argument("--target-root", type=Path, required=True)
    migrate.add_argument("--audit-output", type=Path)
    migrate.add_argument("--semantic-output", type=Path)

    compare = subparsers.add_parser("compare", help="compare two private semantic manifests")
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    return parser


def _print_plan(plan) -> None:
    counts = plan.audit.to_dict()["counts"]
    print(f"CMB evidence: {counts['cmb_evidence']}")
    print(f"CMB SourceRecords: {counts['cmb_source_records']}")
    print(f"Manual evidence: {counts['manual_evidence']}")
    print(f"Transactions: {counts['transactions']}")
    print(f"SourceLinks: {counts['source_links']}")
    print(f"Enrichment decisions: {counts['enrichment_decisions']}")
    print(f"Scheduled rules: {counts['scheduled_rules']}")
    print(f"Feedback: {counts['feedback']}")


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "compare":
        compare_semantic_manifests(
            read_semantic_manifest(args.reference),
            read_semantic_manifest(args.candidate),
        )
        print("Semantic parity: PASS")
        return

    plan = build_migration_plan(args.legacy_root)
    _print_plan(plan)
    if args.audit_output is not None and args.command == "plan":
        _write_json(args.audit_output, plan.audit.to_dict())
    if args.semantic_output is not None:
        _write_json(args.semantic_output, semantic_manifest_from_plan(plan))
    if args.command == "plan":
        print("Migration plan: PASS")
        return

    result = execute_migration(
        plan,
        args.target_root,
        audit_output=args.audit_output,
    )
    print(f"Canonical target: {result.target_root}")
    print(f"Reused SourceRecords: {result.reused_source_record_count}")
    print("Migration materialization: PASS")


if __name__ == "__main__":
    main()
