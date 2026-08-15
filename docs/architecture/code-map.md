# Family Spending Insights — Canonical Code Map

> Status: **authoritative migration map**
> Current-code baseline: `main @ 21a3c88514d7241c6d3c787e70975a461afa0f5a`
> Target architecture: [`system-architecture.md`](./system-architecture.md)

## 1. Purpose

本文件回答四个问题：

1. 当前模块真正承担什么责任；
2. 该责任在 Canonical Architecture 中属于哪里；
3. 当前实现是可以保留的正式模型，还是历史演进形成的 transitional structure；
4. Parallel Rebuild 时应 Keep / Rebuild / Split / Remove 哪些部分。

它不是“文件移动清单”。迁移优先改变依赖方向与数据契约，最后才做最终目录落位。

## 2. Status Vocabulary

只使用以下状态描述当前实现：

- **Canonical Concept** — 概念和核心语义可以直接进入新架构，代码仍可重新组织。
- **Behavior Reference** — 业务行为已验证，应通过 parity 保留，但实现不直接复用。
- **Transitional** — 当前可运行，但责任或依赖边界不符合目标架构。
- **Migration Required** — 数据模型或 identity contract 必须显式迁移。
- **Legacy** — 只保留历史/兼容价值，不进入最终 Backend。
- **Planned** — Canonical Architecture 已预留，但当前产品尚未实现。

## 3. Target Package Map

```text
src/family_spending/
├── domain/
├── sources/
├── application/
│   └── ports/
├── projections/
├── intelligence/
├── persistence/
│   └── filesystem/
├── runtime/
├── interfaces/
│   ├── http/
│   └── cli/
└── config.py
```

最终不保留 `backend/` 作为“所有非纯领域代码”的汇总目录，也不把 storage implementation 混在 package root。

## 4. Backend Module Map

| Current module | Current responsibility | Canonical responsibility / target | Action | Status |
| --- | --- | --- | --- | --- |
| `source_records.py` | `SourceRecord` + `SourceAdapter` | `domain/source.py` + source port | Split interface from model | Canonical Concept |
| `transactions.py` | Transaction core、SourceLink、identity helpers | `domain/transaction.py` | Reimplement/preserve semantics | Canonical Concept |
| `reconciliation.py` | Generic matching + CMB/Manual-specific policy | `domain/reconciliation.py` + `sources/*/reconciliation.py` | Split | Behavior Reference |
| `transaction_resolution.py` | Concrete Source orchestration、domain assembly、Enrichment initialization、diagnostics | No direct target file | Dissolve responsibilities into Application/Domain/Runtime | Legacy / Migration Required |
| `enrichment.py` | Enrichment semantics、review signals、current materialization | `domain/enrichment.py` | Rebuild around sparse decision + resolved view | Migration Required |
| `mapping.py` | Domain Mapping + YAML I/O + paths + resolver | `domain/mapping.py` + `persistence/filesystem/mapping_store.py` | Split | Transitional |
| `mapping_review.py` | Review aggregation/plan + YAML mutation | `application/mapping_review.py` + MappingStore | Rebuild deterministic plan/apply | Behavior Reference |
| `refund_reconciliation.py` | Refund matching / NetConsumption | `domain/refund.py` | Preserve rules through parity | Canonical Concept |
| `month_coverage.py` | Natural-month completeness | `projections/month_coverage.py` | Preserve rules | Canonical Concept |
| `spending_projection.py` | Spending projection + persistence | `projections/spending.py` + ProjectionStore | Split pure projection and I/O | Behavior Reference |
| `financial_projection.py` | Income/net spending/cashflow projection + persistence | `projections/financial.py` + ProjectionStore | Split | Behavior Reference |
| `manual_source.py` | Manual source model + adapter + JSONL store + legacy merchant/category | `sources/manual/*` + EvidenceStore | Remove legacy fields, split I/O | Migration Required |
| `scheduled_input.py` | Rule model + execution metadata + JSON persistence | `domain/scheduling.py` + `application/scheduled_input.py` + ScheduleStore | Split rule/cursor/store | Migration Required |
| `feedback.py` | Feedback model + JSONL persistence | `application/feedback.py` + FeedbackStore | Split | Behavior Reference |
| `source_link_store.py` | SourceRecord↔Transaction identity persistence | `persistence/filesystem/identity_store.py` | Preserve durable semantics | Canonical Concept |
| `enrichment_store.py` | Persist complete materialized Enrichment | `persistence/filesystem/enrichment_store.py` | Replace with sparse `EnrichmentDecision` store | Migration Required |
| `settings.py` | hard-coded data paths + CMB config + credentials loader | `config.py` + StorageLayout + source config | Replace | Transitional |

## 5. CMB Source Map

| Current module | Current responsibility | Canonical target | Action | Status |
| --- | --- | --- | --- | --- |
| `ingestion/imap_163.py` | IMAP acquisition + immutable EML save | `sources/cmb_email/connector.py` | Rebuild around source config/port | Behavior Reference |
| `ingestion/cmb_email_transactions.py` | EML parser + current `CmbTransaction` + CSV rebuild/store | `sources/cmb_email/parser.py` + optional derived export/cache store | Parser becomes SourceRecord-facing; CSV downgraded | Migration Required |
| `ingestion/cmb_source_adapter.py` | `CmbTransaction → SourceRecord` | `sources/cmb_email/adapter.py` | Rebuild with stable evidence identity | Migration Required |

### 5.1 Critical identity gap

当前 CMB legacy id 基于：

```text
source_email + source_index
```

其中 `source_index` 由当前 parser 输出顺序决定。Canonical Source identity 必须换成 evidence-anchored stable locator。现有 SourceLink 中的 CMB source ids 需要一次性 migration；不能在普通 rebuild 时静默变化。

## 6. Current `backend/` Package Map

| Current module | Current responsibility | Canonical target | Action | Status |
| --- | --- | --- | --- | --- |
| `backend/paths.py` | 所有 file path 聚合 | `persistence/filesystem/layout.py` | Replace with `data_root`-based StorageLayout | Transitional |
| `backend/state.py` | 从 current files rehydrate joined snapshot | `runtime/state.py` + Store ports | Rebuild against Canonical storage | Transitional |
| `backend/pipeline.py` | Source Sync + direct reads/writes + Projection rebuild | `application/source_sync.py` | Major rebuild; no concrete file I/O | Transitional |
| `backend/runtime.py` | cached snapshot lifecycle + fingerprint refresh | `runtime/state.py` / coordinator | Keep snapshot concept, replace external-file polling semantics | Canonical Concept |
| `backend/application.py` | large facade implementing many use cases | thin facade + `application/*` use cases | Split heavily | Transitional |
| `backend/manual_commands.py` | Manual create/correct/delete UoW orchestration | `application/manual_input.py` | Rebuild on SourceSync port | Behavior Reference |
| `backend/scheduled_jobs.py` | due occurrence batch + Source sync | `application/scheduled_input.py` + runtime scheduler trigger | Rebuild | Behavior Reference |
| `backend/projection_queries.py` | projection read queries | `application/queries.py` | Rebuild | Behavior Reference |
| `backend/http_server.py` | HTTP routing/transport around Application | `interfaces/http/` | Preserve external API contract, rebuild internals | Behavior Reference |

`backend/` 当前把 Application、Runtime、Pipeline、State、HTTP 放在一个 package，是历史演进结果。最终目录应按 architecture layer，而不是按“backend everything”组织。

## 7. Infrastructure Map

当前 `infrastructure/` 主要只有 `file_uow.py`，而 filesystem adapters 分散在 root modules。

| Current module | Canonical target | Action | Status |
| --- | --- | --- | --- |
| `infrastructure/file_uow.py` | `persistence/filesystem/unit_of_work.py` | Preserve concept / reimplement | Canonical Concept |
| root-level `read_*` / `write_*` functions | dedicated filesystem Stores | Move responsibility behind ports | Transitional |

FileUnitOfWork 的“先 plan、后 coordinated commit、失败 rollback”语义继续保留；具体文件清单由 Store/Layout 组合提供，而不是由 Domain 知道路径。

## 8. Interface Map

| Current | Canonical target | Action | Status |
| --- | --- | --- | --- |
| `cli.py`, `__main__.py` | `interfaces/cli/` | Rebuild without second pipeline | Behavior Reference |
| `backend/http_server.py` | `interfaces/http/` | Keep external contract | Behavior Reference |

HTTP / CLI 都只能调用 Application use cases，不直接读取 household financial files。

## 9. Frontend Map

当前前端总体结构已经符合长期边界：

```text
frontend/
├── apps/
│   ├── web/
│   └── mini/
└── packages/
    ├── core/
    └── design-tokens/
```

| Current | Canonical direction | Action | Status |
| --- | --- | --- | --- |
| `frontend/packages/core` | API contracts/service/shared semantics | Keep | Canonical Concept |
| `frontend/apps/web` | full Desktop workspace | Keep | Canonical Concept |
| `frontend/apps/mini` | lightweight Mini presentation | Keep | Canonical Concept |
| `local_dashboard/` | historical fallback | remove after canonical backend + current frontends prove stable | Legacy |

Backend rebuild 不借机重构现有正式前端。新 Backend 原生实现当前正式 API contract；不通过 compatibility shim 转发旧 Backend。

## 10. Persistent Data Map

| Current data | Canonical class | Canonical target | Action |
| --- | --- | --- | --- |
| `data/emails/*.eml` | Source Evidence | `evidence/cmb-email/` | Preserve exact bytes |
| `data/manual_source_records.jsonl` | Source Evidence + legacy optional enrichment | `evidence/manual/records.jsonl` | migrate source facts; discard migrated legacy duplication |
| `data/transaction_source_links.jsonl` | Durable Identity Decision | `state/identity/source-links.jsonl` | migrate CMB source ids; preserve Transaction ids/relationships |
| `data/mappings/*.yaml` | Reviewed Configuration | `state/mappings/` | preserve semantics |
| `data/enrichment_state.jsonl` | mixed Durable Decision + Derived Enrichment | `state/enrichment/decisions.jsonl` | extract sparse user decisions only |
| `data/scheduled_input_rules.json` | Config + execution cursor mixed | `state/schedules/rules.json` + `execution.json` | split |
| `data/feedback.jsonl` | Durable Product State | `state/feedback/feedback.jsonl` | preserve |
| `data/transactions.csv` | Parsed CMB derived artifact | `derived/sources/` or export-only | rebuild, no longer truth |
| `data/reports/*.json` | Derived Projection | `derived/projections/` | rebuild |
| future suggestion indexes | Runtime / Derived | runtime or `derived/indexes/` | never backup truth |

## 11. Planned Extension Points

这些是 Canonical 预留，不代表 Parallel Rebuild 初期必须实现新产品 feature：

- `SourceRegistry`
- `MappingSuggestionEngine`
- Mapping import/export
- Transaction export
- additional Projections
- additional Sources

Rebuild 第一目标是实现当前业务语义 parity；Planned extension point 只需要在结构上可自然加入，避免提前实现未验证 feature。

## 12. Dependency Map

期望依赖方向：

```text
interfaces ───────► application ◄──── runtime
                         │
                         ▼
                       domain

sources ──implements──► application ports
persistence ──────────► application ports

projections consume domain/application state
intelligence consumes read models and returns suggestions only
```

### Forbidden dependencies

- `domain -> persistence`
- `domain -> sources connector`
- `domain -> interfaces`
- `application -> concrete YAML/JSONL/CSV parsing`
- `projection -> mutation store`
- `intelligence -> Mapping write`
- `frontend -> household files`
- `new backend -> old backend`
- `old backend -> new rebuild backend`

## 13. Migration Priority

不是按文件名移动，而按下面的依赖顺序重建：

```text
Canonical Domain + Storage Contract
        ↓
Stable Source Identity + Evidence Stores
        ↓
Durable Identity + Enrichment Decision model
        ↓
Generic Source Sync
        ↓
Projection
        ↓
Runtime / Coordinator / Supervisor
        ↓
Application Use Cases
        ↓
HTTP / CLI
        ↓
Migration + Parity + Atomic Cutover
```

详细阶段和 gate 见 [`rebuild-strategy.md`](./rebuild-strategy.md)。

## 14. How to Use This Map

开发新代码前：

1. 在本表找到当前 responsibility；
2. 确认 target boundary；
3. 如果状态为 `Migration Required`，不要直接复制旧数据模型；
4. 如果状态为 `Behavior Reference`，通过测试/parity 保存行为，不要求代码复用；
5. 如果状态为 `Legacy`，不要把旧 abstraction 带入新代码；
6. 如果状态为 `Planned`，只保留 extension seam，不提前实现产品功能。

最终 Cutover 后，本表应更新为“Current = Canonical”，并删除不再需要的迁移说明。
