from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from family_spending.config import AppConfig, load_app_config
from family_spending.interfaces.http.server import create_http_server
from family_spending.runtime.composition import RuntimeComponents, compose_runtime

DEFAULT_CONFIG_PATH = Path("family-spending.toml")


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Build the single canonical operator surface without duplicating business workflows."""
    parser = argparse.ArgumentParser(prog="family-spending")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="runtime TOML config path (default: family-spending.toml)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="bootstrap and serve the JSON API")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    subparsers.add_parser("sync", help="run canonical Source synchronization")

    jobs = subparsers.add_parser("jobs", help="run backend orchestration jobs")
    jobs_subparsers = jobs.add_subparsers(dest="job", required=True)
    run_due = jobs_subparsers.add_parser(
        "run-due",
        help="materialize Scheduled Input occurrences due through a date",
    )
    run_due.add_argument("--as-of", type=_iso_date)

    rebuild = subparsers.add_parser("rebuild", help="rebuild derived runtime state")
    rebuild_subparsers = rebuild.add_subparsers(dest="rebuild_target", required=True)
    rebuild_subparsers.add_parser(
        "projections",
        help="rebuild projections from canonical persistent state",
    )

    diagnose = subparsers.add_parser("diagnose", help="inspect coherent canonical state")
    diagnose_subparsers = diagnose.add_subparsers(dest="diagnose_target", required=True)
    diagnose_subparsers.add_parser(
        "state",
        help="summarize the current Transaction/Enrichment snapshot",
    )
    return parser


def _serve(config: AppConfig, components: RuntimeComponents, host: str | None, port: int | None) -> None:
    components.application.initialize()
    bind_host = host or config.server.host
    bind_port = config.server.port if port is None else port
    if not 0 <= bind_port <= 65535:
        raise ValueError("server port must be between 0 and 65535")
    server = create_http_server(components.application, bind_host, bind_port)
    actual_host, actual_port = server.server_address[:2]
    print(f"Family Spending API: http://{actual_host}:{actual_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _minor_to_decimal(value: object) -> str:
    minor = int(value)
    return format(Decimal(minor) / Decimal(100), "f")


def _sync(components: RuntimeComponents) -> None:
    result = components.application.sync_sources()
    spending = components.application.get_spending_statistics()
    all_data = spending["summary"]["all_data"]
    print(f"Source records: {result.source_record_count}")
    print(f"Transactions: {result.transaction_count}")
    print(f"Created identities: {result.created_count}")
    print(f"Matched identities: {result.matched_count}")
    print(f"Reused identities: {result.reused_count}")
    print(f"Months: {all_data['month_count']}")
    print(f"Total net spending: {_minor_to_decimal(all_data['total_spending_minor'])}")


def _run_due(components: RuntimeComponents, as_of: date | None) -> None:
    result = components.application.run_due_scheduled_inputs(as_of or date.today())
    print(f"Scheduled occurrences generated: {len(result.occurrences)}")


def _rebuild_projections(components: RuntimeComponents) -> None:
    """Report projections rebuilt during canonical Runtime composition from durable state."""
    transactions = components.application.list_transactions()
    spending = components.application.get_spending_statistics()
    all_data = spending["summary"]["all_data"]
    shown_data = spending["summary"]["shown_data"]
    print(f"Transactions: {len(transactions)}")
    print(f"Months: {all_data['month_count']}")
    print(f"Total net spending: {_minor_to_decimal(all_data['total_spending_minor'])}")
    print(f"Shown months: {shown_data['month_count']}")
    print(f"Shown net spending: {_minor_to_decimal(shown_data['total_spending_minor'])}")


def _diagnose_state(components: RuntimeComponents) -> None:
    state = components.runtime.current_state()
    transactions = state.household.transactions
    expense_count = sum(item.transaction_type == "expense" for item in transactions)
    income_count = len(transactions) - expense_count
    print(f"Transactions: {len(transactions)}")
    print(f"Expense transactions: {expense_count}")
    print(f"Income transactions: {income_count}")
    print(f"Unclassified transactions: {len(state.indexes.unclassified_transaction_ids)}")
    print(f"Source links: {len(state.household.source_links)}")
    print(f"Pending Source records: {len(state.household.unreconciled_source_record_ids)}")
    print(f"Runtime generation: {state.generation}")


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = load_app_config(args.config)
    components = compose_runtime(config)

    if args.command == "serve":
        _serve(config, components, args.host, args.port)
        return
    if args.command == "sync":
        _sync(components)
        return
    if args.command == "jobs" and args.job == "run-due":
        _run_due(components, args.as_of)
        return
    if args.command == "rebuild" and args.rebuild_target == "projections":
        _rebuild_projections(components)
        return
    if args.command == "diagnose" and args.diagnose_target == "state":
        _diagnose_state(components)
        return
    raise RuntimeError("Unhandled Family Spending command")
