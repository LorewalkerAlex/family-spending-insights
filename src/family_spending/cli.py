from __future__ import annotations

import argparse
from datetime import date
from typing import Sequence

from family_spending.backend import BackendPaths, BackendRuntime
from family_spending.backend.application import FamilySpendingApplication
from family_spending.backend.http_server import create_http_server
from family_spending.backend.pipeline import HouseholdSyncSummary


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    """Build the single operator-facing command surface for the backend."""
    parser = argparse.ArgumentParser(prog="family-spending")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="bootstrap and serve the local JSON API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    subparsers.add_parser("sync", help="run the full Source synchronization pipeline")

    jobs = subparsers.add_parser("jobs", help="run backend orchestration jobs")
    jobs_subparsers = jobs.add_subparsers(dest="job", required=True)
    run_due = jobs_subparsers.add_parser(
        "run-due",
        help="materialize Scheduled Input occurrences due through a date",
    )
    run_due.add_argument("--as-of", type=_iso_date)

    rebuild = subparsers.add_parser("rebuild", help="rebuild derived backend state")
    rebuild_subparsers = rebuild.add_subparsers(dest="rebuild_target", required=True)
    rebuild_subparsers.add_parser(
        "projections",
        help="rebuild Analytics/Projection without rerunning Reconciliation",
    )

    diagnose = subparsers.add_parser("diagnose", help="inspect coherent persisted state")
    diagnose_subparsers = diagnose.add_subparsers(dest="diagnose_target", required=True)
    diagnose_subparsers.add_parser(
        "state",
        help="summarize the current reconciled Transaction/Enrichment snapshot",
    )
    return parser


def _serve(host: str, port: int) -> None:
    application = FamilySpendingApplication()
    application.initialize()
    server = create_http_server(application, host, port)
    print(f"Family Spending API: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _format_household_sync_summary(summary: HouseholdSyncSummary) -> str:
    return "\n".join(
        (
            f"Raw transactions: {summary.raw_transactions}",
            f"Zero-amount transactions ignored: {summary.zero_amount_transactions}",
            f"Refund transactions: {summary.refund_transactions}",
            f"Same-merchant refund matches: {summary.same_merchant_refund_matches}",
            f"Same-merchant matched amount: {format(summary.same_merchant_matched_amount, 'f')}",
            f"Net consumption transactions: {summary.net_consumption_transactions}",
            f"Fully refunded transactions: {summary.fully_refunded_transactions}",
            f"Partially refunded transactions: {summary.partially_refunded_transactions}",
            f"Unmatched refunds: {summary.unmatched_refund_count}",
            f"Unmatched refund amount: {format(summary.unmatched_refund_amount, 'f')}",
            f"Unclassified net transactions: {summary.unclassified_net_transactions}",
            f"Months: {summary.months}",
            f"Total net spending: {format(summary.total_net_spending, 'f')}",
            f"Shown months: {summary.shown_months}",
            f"Shown net spending: {format(summary.shown_net_spending, 'f')}",
        )
    )


def _sync() -> None:
    runtime = BackendRuntime(BackendPaths())
    print(_format_household_sync_summary(runtime.sync_sources()))


def _run_due(as_of: date | None) -> None:
    application = FamilySpendingApplication()
    application.runtime.bootstrap()
    result = application.run_due_scheduled_inputs(as_of=as_of)
    print(f"Scheduled occurrences generated: {len(result.occurrences)}")


def _rebuild_projections() -> None:
    runtime = BackendRuntime(BackendPaths())
    summary = runtime.rebuild_projections()
    print(f"Transactions: {summary.transactions}")
    print(f"Months: {summary.months}")
    print(f"Total net spending: {format(summary.total_net_spending, 'f')}")
    print(f"Shown months: {summary.shown_months}")
    print(f"Shown net spending: {format(summary.shown_net_spending, 'f')}")


def _diagnose_state() -> None:
    runtime = BackendRuntime(BackendPaths())
    snapshot = runtime.refresh()
    expense_count = sum(
        transaction.transaction_type == "expense"
        for transaction in snapshot.transactions
    )
    income_count = len(snapshot.transactions) - expense_count
    unclassified_count = sum(
        enrichment.is_unclassified
        for enrichment in snapshot.enrichments_by_transaction_id.values()
    )
    print(f"Transactions: {len(snapshot.transactions)}")
    print(f"Expense transactions: {expense_count}")
    print(f"Income transactions: {income_count}")
    print(f"Unclassified transactions: {unclassified_count}")
    print(f"Source links: {len(snapshot.source_links)}")


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        _serve(args.host, args.port)
        return
    if args.command == "sync":
        _sync()
        return
    if args.command == "jobs" and args.job == "run-due":
        _run_due(args.as_of)
        return
    if args.command == "rebuild" and args.rebuild_target == "projections":
        _rebuild_projections()
        return
    if args.command == "diagnose" and args.diagnose_target == "state":
        _diagnose_state()
        return
    raise RuntimeError("Unhandled Family Spending command")
