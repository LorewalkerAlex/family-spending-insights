# Family Spending Insights Backend Technical Architecture

> Status: canonical backend baseline after runtime/pipeline consolidation and orchestration teardown
> Scope: Python backend runtime, application orchestration, pipeline execution, file-backed commit boundaries, operator CLI, and local JSON HTTP transport.

## 1. Purpose

`family-consumption-data-architecture-design.md` defines the domain and HLD: Source, Source Record, Reconciliation, Transaction, Enrichment, Analytics / Projection, and the rule that a change starts from its own stage and only refreshes downstream stages.

This document defines how the current Python implementation realizes that model as a lightweight local modular monolith. It intentionally does **not** introduce a database, message bus, dependency-injection framework, persistent cache, or service decomposition merely to make the repository look more layered.

The architecture goal is concrete:

```text
one current backend state
+ explicit pipeline entry points
+ explicit commit boundaries
+ one canonical Application / HTTP / operator surface
+ no parallel feature-level orchestration path
```

## 2. Technical shape

The current backend execution shape is:

```text
Interfaces
├── python -m family_spending ...
└── local JSON HTTP
          ↓
Application
└── FamilySpendingApplication
          ↓
BackendRuntime
├── CurrentHouseholdSnapshot
├── HouseholdPipeline
├── ManualInputCommandService
└── ScheduledInputJobRunner
          ↓
Domain / existing business rules
├── Source Adapter / Reconciliation
├── Transaction / Enrichment
├── Mapping / Refund
└── Analytics / Projection
          ↓
Infrastructure
└── FileUnitOfWork + existing file stores
          ↓
local CSV / JSONL / YAML / EML / Projection JSON
```

This is a **modular monolith**, not a distributed architecture. The file system remains the persistent source of truth for the current local product.

## 3. Package responsibilities

### `family_spending.backend`

The `backend/` package contains runtime/application orchestration that previously existed implicitly inside feature-oriented modules.

```text
backend/
├── paths.py
├── state.py
├── pipeline.py
├── runtime.py
├── application.py
├── manual_commands.py
├── scheduled_jobs.py
├── projection_queries.py
└── http_server.py
```

- `paths.py` collects the persistent files that participate in the financial runtime.
- `state.py` reconstructs a coherent already-reconciled `CurrentHouseholdSnapshot` without rerunning identity decisions.
- `pipeline.py` owns explicit Source Sync and downstream Projection rebuild lifecycles, including the correction semantics needed when one Manual Source identity replaces another.
- `runtime.py` owns the in-process current snapshot and its refresh lifecycle.
- `application.py` is the canonical Application/API boundary. It owns client validation and view assembly while delegating Source mutation, scheduling, Mapping/Enrichment mutation, and Projection reads to explicit backend services.
- `manual_commands.py` owns Manual Input create / correct / delete orchestration over one Source-sync plan and one shared file UoW.
- `scheduled_jobs.py` batches Scheduled Input catch-up and submits the resulting Manual Source candidates through one Source Sync.
- `projection_queries.py` reads generated Projection JSON for read-only Application queries without hiding a rebuild behind GET.
- `http_server.py` is the canonical local JSON HTTP transport. The handler delegates all financial reads and mutations to `FamilySpendingApplication` and performs no direct financial file I/O.

### `family_spending.infrastructure`

`infrastructure/file_uow.py` provides the shared file-backed Unit of Work. Existing stores still own file formats and writes; the UoW owns only the cross-file commit/rollback boundary.

### Existing root domain/storage modules

Validated domain, source, projection, and file-format modules remain at `family_spending/` package root when their responsibility is already singular and moving them would only create path churn. Examples include `reconciliation.py`, `transactions.py`, `enrichment.py`, `mapping.py`, `manual_source.py`, `scheduled_input.py`, `spending_projection.py`, and `transaction_resolution.py`.

These root modules are not alternate Application entry paths. `scheduled_input.py` owns the monthly rule model, persistence, calendar advance, and deterministic occurrence identity only; due execution belongs to `backend/scheduled_jobs.py`. `transaction_resolution.py` owns shared household domain assembly and pure review helpers used by `HouseholdPipeline` and tests; operator lifecycle belongs to `family_spending.cli`.

There is now one Application/HTTP/runtime orchestration path. Physical relocation into deeper `domain/` or `storage/` folders is optional future organization work, not unfinished backend migration.

## 4. CurrentHouseholdSnapshot

`CurrentHouseholdSnapshot` is the current joined read model used by runtime-backed Queries and downstream-only mutations.

It contains:

```text
source_records
manual_entries
source_links
transactions
transactions_by_id
authoritative source_records_by_transaction_id
enrichment_states
enrichment_states_by_transaction_id
materialized enrichments_by_transaction_id
mappings
```

Loading a snapshot performs consistency validation but does **not** run Reconciliation. In particular it rejects:

- Source Records that have no current Source Link;
- stale Source/Transaction link groups;
- Transactions missing persisted Enrichment state;
- invalid persisted Category state against the current Mapping configuration.

Those failures mean the persisted stages are no longer coherent and a Source Sync is required; a read request must not silently invent new identity decisions.

## 5. BackendRuntime lifecycle

`BackendRuntime` owns one optional in-memory `CurrentHouseholdSnapshot`.

### Bootstrap

```text
BackendRuntime.bootstrap()
→ sync_sources()
→ HouseholdPipeline Source Sync
→ persist coherent downstream state
→ refresh()
→ publish CurrentHouseholdSnapshot
```

A normal `serve` startup bootstraps once before accepting client traffic, then executes due Scheduled Input orchestration.

### Snapshot-backed Query reuse

```text
HTTP Query
→ FamilySpendingApplication
→ BackendRuntime.current_state()
→ cached CurrentHouseholdSnapshot
```

`BackendRuntime` records a cheap fingerprint for the persistent files relevant to current state: CMB transactions, Manual Source, Source Links, Enrichment state, Merchant Mapping, and Category Mapping.

If those files have not changed, repeated Queries reuse the same snapshot instead of re-reading and rebuilding the world for every request.

If a tracked file changes externally, `current_state()` performs `refresh()`, which reloads already-reconciled state without Reconciliation. If an external Source change makes links stale, refresh fails explicitly and the caller must run Source Sync.

A failed file-backed mutation can restore the original bytes while changing filesystem metadata such as mtime. In that case the next `current_state()` may legitimately reload an equivalent snapshot. Runtime correctness therefore depends on coherent observable state, not Python object identity across rollback.

The runtime snapshot is a **rebuildable in-process read model**, not a new persistent authority.

### Generated Projection reads

Generated report queries have a different read source from snapshot-backed entity queries. `FamilySpendingApplication.get_spending_statistics()` and `get_financial_summary()` delegate to `projection_queries.py`, which validates and reads the already-generated Projection documents. The canonical HTTP handler calls those Application methods; it does not open financial Projection files itself. A GET therefore exposes current persisted Projection state without running Source Sync, Reconciliation, or Projection rebuild.

## 6. Pipeline entry points

The technical runtime currently recognizes three financial processing scopes.

### 6.1 Source Sync

Use when Source facts or Source identity relationships can change.

```text
CMB / Manual Source facts
→ existing Source Links + Enrichment
→ Mapping
→ build_household_domain_state()
→ Reconciliation / Transaction
→ preserve or initialize Enrichment
→ Refund / Analytics / Projections
→ FileUnitOfWork
→ Source Links + Enrichment + both Projections
→ Runtime refresh
```

`HouseholdPipeline.plan_source_sync()` evaluates the candidate next state before persistence. `write_source_sync_plan()` writes an already evaluated plan inside an owning UoW.

The planning API also accepts explicit Manual command context instead of forcing each caller to duplicate reconciliation details:

- `submitted_source_ids` identifies newly submitted Manual Sources whose non-null Note must update current Enrichment even when the new Source matches an existing Transaction;
- `ManualSourceReplacement` carries correction context so the pipeline can preserve the prior Transaction identity when the corrected authoritative Manual Source still represents the same unmatched real-world transaction;
- when a correction instead uniquely matches another existing Transaction, normal Reconciliation wins and the replacement Source converges onto that Transaction;
- when Transaction identity is preserved, description Mapping is reapplied only if current Merchant still follows the old description Mapping; explicit Merchant / Category exceptions remain user-owned;
- correction Note is changed only when the command explicitly requested a Note update.

This plan/write split allows Manual Input and Scheduled Input to include their own authoritative files in a larger commit boundary without performing redundant complete Source → Projection rebuilds.

### 6.2 Enrichment / Mapping downstream mutation

Use when Source identity does not change.

Current runtime-backed examples are transaction Enrichment PATCH and Mapping Review Apply:

```text
CurrentHouseholdSnapshot
→ change Enrichment / Mapping
→ materialize current Enrichment
→ Refund / Analytics / Projections
→ FileUnitOfWork
→ persist affected authoritative state + both Projections
→ Runtime refresh
```

These paths deliberately do **not** rerun Source Adapter or Reconciliation.

### 6.3 Projection rebuild

Use when only downstream analytics/projection code or serialized outputs need rebuilding.

```text
already-reconciled CurrentHouseholdSnapshot
→ Refund / Analytics
→ spending_statistics.json
→ financial_summary.json
```

Operator command:

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending rebuild projections
```

This path does not rewrite Source identity, Source Links, or Enrichment.

## 7. Manual Input runtime commands

Manual Input create / correct / delete use `ManualInputCommandService` as the single Source-mutation command family.

All three commands start from `BackendRuntime.current_state()`, evaluate one candidate Source-sync plan, and commit their authoritative Manual Source change together with all downstream files:

```text
Runtime current snapshot
→ candidate Manual Source set
→ one HouseholdPipeline.plan_source_sync()
→ one FileUnitOfWork
   ├── Manual Source
   ├── Source Links
   ├── Enrichment
   ├── spending Projection
   └── financial Projection
→ Runtime refresh
```

Create appends one source-native Manual record and uses its actual Reconciliation decision for `created` / `matched` / `reused` behavior.

Correction replaces the Source Record identity rather than editing Transaction Core in place. The pipeline preserves the established correction semantics:

- an unmatched manual-only correction keeps the prior Transaction identity;
- a replacement that uniquely matches another current Transaction converges to that Transaction instead;
- Mapping-following Merchant/default Category can follow the corrected description;
- explicit Merchant or Category exceptions remain attached to the preserved Transaction;
- omitted Note preserves current Enrichment Note, while an explicitly supplied Note, including null, updates it.

Delete removes only the selected Manual Source from the candidate Source set. The resulting Source Sync determines whether its Transaction survives because another authoritative/supporting Source still backs it.

A normal captured failure restores Manual Source, Source Links, Enrichment, and both Projections through the shared `FileUnitOfWork`; the command never publishes a partially written runtime state.

## 8. Scheduled Input batching

Scheduled Input remains orchestration, not a financial Source type.

The runtime runner works as:

```text
Scheduled rules
→ calculate all occurrences due through as_of
→ stable Manual Source IDs
→ collect new Manual Source entries
→ one HouseholdPipeline.plan_source_sync()
→ resolve occurrence transaction/action results
→ one FileUnitOfWork
   ├── Manual Source
   ├── Source Links
   ├── Enrichment
   ├── both Projections
   └── final Scheduled Rule cursor
→ Runtime refresh
```

A rule that is three months behind therefore does not perform three complete Source → Projection rebuilds. All due occurrences are evaluated together and committed as one batch.

Stable `rule_id + occurrence_date` Source identity preserves idempotency. If a previously persisted occurrence already has a valid Source Link while the rule cursor is stale, the runner reports it as recovered instead of creating another financial event.

V1 still has no daemon or system scheduler. It runs during Application initialization, rule mutation when due work must execute, or explicit `jobs run-due` / API Run Due.

## 9. FileUnitOfWork

`FileUnitOfWork` centralizes a pattern that was previously duplicated in feature modules.

At entry it captures exact bytes (or absence) for every declared participant. Existing stores perform their normal writes. The caller must then explicitly `commit()`.

```text
with FileUnitOfWork(paths):
    write authoritative state
    write derived state
    refresh runtime if required
    commit()
```

If an exception escapes, or the context exits without `commit()`, participants are restored in reverse order. A secondary restoration failure is raised explicitly as `FileUnitOfWorkRollbackError`.

This is an **application-level local file transaction**, not a durable database transaction or crash journal. It protects coordinated mutations from ordinary captured failures; process-level crash durability remains bounded by the atomic-write behavior of the individual stores.

## 10. Canonical Application boundary

`family_spending.backend.application.FamilySpendingApplication` is the only local Application/API orchestration class. It is backed by one `BackendRuntime`; there is no base Application subclass or parallel feature-level write path.

Its responsibilities are deliberately narrow:

- normalize and validate client-facing command values;
- assemble Transaction / Manual Input / Mapping Review views from `CurrentHouseholdSnapshot`;
- expose generated Spending Statistics and Financial Summary through `projection_queries.py`;
- delegate Manual Input mutations to `ManualInputCommandService`;
- delegate due Scheduled Input execution to `ScheduledInputJobRunner`;
- coordinate Mapping Review and transaction Enrichment downstream mutations through `FileUnitOfWork`;
- manage local product Feedback, which remains outside the financial pipeline.

The HTTP server and CLI both construct this same Application boundary. Query, command, and rollback semantics therefore have one implementation path.

## 11. Operator CLI and process lifecycle

The canonical backend operator surface is:

```text
python -m family_spending
├── serve
├── sync
├── jobs
│   └── run-due
├── rebuild
│   └── projections
└── diagnose
    └── state
```

Meanings:

- `serve`: bootstrap current Source state, run due orchestration, then serve the canonical runtime JSON API through `backend/http_server.py`;
- `sync`: full Source Sync and downstream Projection refresh;
- `jobs run-due`: materialize Scheduled Input occurrences due through today or `--as-of`;
- `rebuild projections`: downstream-only rebuild from already-reconciled state;
- `diagnose state`: read and summarize coherent current state without mutation.

The JavaScript managed development runtime starts its API worker through the same canonical command:

```text
npm run dev
→ scripts/dev-runtime.mjs
→ python -m family_spending serve --port <managed-port>
```

Desktop and Mini H5 proxies therefore hit the same backend runtime that the standalone operator CLI uses. The server delegates financial reads to `FamilySpendingApplication`; for example `GET /api/spending-statistics` reads the already-generated schema-v2 projection through the Application query boundary and does not run Source Sync or Projection rebuild.

## 12. Dependency direction

The desired dependency direction is:

```text
interfaces / CLI
      ↓
application / runtime orchestration
      ↓
domain rules
      ↑
infrastructure implementations
```

The physical package layout intentionally keeps stable domain/storage modules shallow. Dependency correctness is defined by responsibility and call direction rather than by forcing every module into a deeper folder tree:

- Query/UI and HTTP transport code must not perform financial file I/O or recomputation directly; read-only Projection access belongs behind an Application/query boundary;
- Application commands should call explicit pipeline stages rather than reproduce Source → Projection steps;
- rollback mechanics belong in the shared UoW, not copied into every new feature;
- domain identity rules must remain independent of JSON/YAML/HTTP concerns;
- persistent storage changes should be hidden behind the existing read/write boundaries and future repository abstractions rather than leaking into clients.

## 13. Concurrency and runtime scope

The current backend is a local single-process product. `BackendRuntime` is an in-process state owner; it is not a cross-process cache or lock manager.

The current architecture therefore assumes one authoritative mutation flow per local API process. A future public/multi-user deployment must add an explicit serialized mutation/concurrency strategy and durable storage boundary rather than treating the current file UoW as sufficient for multi-process writes.

This is intentionally deferred until the product actually requires remote concurrent writers.

## 14. Validation baseline

The architecture is protected by dedicated tests for:

- `FileUnitOfWork` commit and rollback;
- full Source Sync and downstream-only Projection rebuild separation;
- runtime snapshot reuse, refresh, and stale-state detection;
- runtime-backed Query / Mapping / Enrichment behavior;
- read-only Spending Statistics / Financial Summary API behavior, including Application-owned Projection access, no Source Sync on GET, missing-projection error handling, and canonical HTTP route behavior;
- Manual Input create / correct / delete through one runtime-owned Source-sync command boundary;
- Manual correction Transaction identity preservation versus convergence to an existing Transaction;
- preservation of explicit Merchant / Category / Note semantics during Manual correction;
- Manual deletion behavior for manual-only versus CMB-backed Transactions;
- rollback of Manual Source, Source Links, Enrichment, and both Projections on mutation failure;
- preservation of transaction-only Mapping/Enrichment exceptions;
- Scheduled catch-up batching, idempotency, recovery, and rollback;
- the canonical CLI parser;
- the managed development runtime launching `family_spending serve`.

The broader existing Python, legacy Dashboard, shared frontend, Desktop, H5, and WeChat build regressions remain the final release gate for architecture changes because the goal is architectural replacement with no product-contract regression.

## 15. Backend rebuild completion and next work

The backend orchestration rebuild is complete at the current local-product boundary. Source mutation, Mapping Review, transaction Enrichment, Scheduled due execution, Projection rebuild/query, CLI, and HTTP now converge on one Runtime / Pipeline / Application model. No second root-level Application, HTTP, Manual Input, or statistics-generation orchestration entry remains.

The remaining root modules are intentionally retained because they own domain rules, source/storage formats, or pure projection logic rather than competing lifecycle orchestration. File count is therefore not a cleanup target by itself.

Future backend work should be driven by product evidence: new Source types, richer analytics, measured performance bottlenecks, public deployment, durable concurrency, or storage requirements. A broad directory move is not a backend milestone unless it materially reduces maintenance cost.
