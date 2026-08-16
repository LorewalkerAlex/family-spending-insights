# Family Spending Insights — Canonical Code Map

> Status: **authoritative current code map**
> Current architecture: [`system-architecture.md`](./system-architecture.md)
> Canonical production cutover: 2026-08-16

## 1. Current package

正式 Backend 当前只有一个 `family_spending` package：

```text
src/family_spending/
├── application/
│   └── ports/
├── domain/
├── interfaces/
│   ├── cli/
│   └── http/
├── persistence/
│   └── filesystem/
├── projections/
├── runtime/
├── sources/
│   ├── cmb_email/
│   └── manual/
├── __init__.py
├── __main__.py
└── config.py
```

Parallel Rebuild 已完成；本文件不再把旧 Backend 当作“current implementation”建立迁移对照。

## 2. Responsibility map

| Package | Responsibility |
| --- | --- |
| `domain/` | SourceRecord、Transaction、SourceLink identity、Reconciliation、Mapping、Enrichment、Refund、Scheduling 等稳定业务模型与纯规则 |
| `application/` | Source Sync、Manual Input、Mapping Review、Enrichment command、Scheduled Input、Feedback、Query 等 use cases |
| `application/ports/` | Runtime、Source、Storage 等行为型依赖契约 |
| `sources/cmb_email/` | CMB acquisition、raw EML evidence、parser、normalization 与 source-specific reconciliation policy |
| `sources/manual/` | Manual Evidence model、Source adapter 与 source-specific reconciliation policy |
| `persistence/filesystem/` | Canonical StorageLayout、manifest、Evidence/Identity/Mapping/Enrichment/Schedule/Feedback stores 与 Unit of Work |
| `projections/` | Month coverage、Spending、Financial 等可重建 projection |
| `runtime/` | Composition Root、RuntimeState、single-writer coordinator 与 supervisor |
| `interfaces/http/` | 正式 JSON transport，只调用 Application |
| `interfaces/cli/` | `serve/sync/jobs/rebuild/diagnose` operator surface，复用同一个 Composition Root |
| `config.py` | TOML 非敏感配置与环境变量 credentials contract |

## 3. Dependency direction

```text
interfaces ───────► application ◄──── runtime
                         │
                         ▼
                       domain

sources ──implements──► application ports
persistence ──────────► application ports

projections consume canonical domain/application state
```

禁止恢复：

- `domain -> persistence`
- `domain -> source connector`
- `domain -> interfaces`
- `application -> concrete filesystem serialization`
- `HTTP/CLI -> household file I/O`
- frontend 直接读取 `data/`
- new canonical code 导入已删除的 legacy Backend modules

这些边界由 `tests/contract/test_architecture.py` 持续验证。

## 4. Canonical persistent data map

根配置 `family-spending.toml` 默认把 data root 指向 `./data`。

| Canonical path | State class | Meaning |
| --- | --- | --- |
| `evidence/cmb-email/` | Source Evidence | Raw EML exact bytes |
| `evidence/manual/records.jsonl` | Source Evidence | Manual source facts |
| `state/identity/source-links.jsonl` | Durable Decision | SourceRecord ↔ Transaction identity history |
| `state/enrichment/decisions.jsonl` | Durable Decision | Sparse merchant/category overrides 与 note |
| `state/mappings/*.yaml` | Reviewed Knowledge | description→merchant、merchant→default category |
| `state/schedules/rules.json` | Configuration | Scheduled rules |
| `state/schedules/execution.json` | Operational State | Schedule execution cursor |
| `state/feedback/feedback.jsonl` | Durable Product State | Feedback |
| `derived/sources/` | Derived | Source inspection/cache artifacts |
| `derived/projections/` | Derived | Spending / Financial projections |
| `derived/indexes/` | Derived | Rebuildable indexes |

整个 `data/` 都是本地 household state，不进入 Git。Mapping 不再作为 repository-tracked legacy YAML 发布。

## 5. Removed legacy structure

Atomic Cutover 后不再存在于正式 Runtime：

- `src/family_spending/backend/`
- `src/family_spending/ingestion/`
- root-level `transaction_resolution.py` / `source_records.py`
- materialized legacy enrichment persistence
- legacy SourceLink / Pipeline / Settings modules
- `rebuild/`
- migration-only runtime compatibility

一次性 Legacy → Canonical migration tooling 保存在 Git 历史 commit `a3ee0436078385d48d71039b3f32825f12742513`，不需要留在生产 source tree。

## 6. Frontend boundary

```text
frontend/
├── apps/
│   ├── web/                  # Desktop React application
│   └── mini/                 # Native WeChat Mini Program project
└── packages/
    ├── core/                 # transport-agnostic Desktop/shared logic where useful
    └── design-tokens/
```

Desktop 与 WeChat Mini 共用正式 Backend/Application HTTP contract，但不要求共用 UI runtime。正式 Mini 从 2026-08-16 起是可直接导入微信开发者工具的原生 TypeScript 小程序：使用 `Page` / WXML / WXSS / `wx.request`，不再维护 Taro、React Mini、Mini H5 或 `dist/` 作为小程序开发入口。

Mini 当前直接导入微信开发者工具的核心结构：

```text
frontend/apps/mini/
├── project.config.json
├── miniprogram/
│   ├── app.ts / app.json / app.wxss
│   ├── config/
│   ├── services/
│   └── pages/
├── test/
├── types/
└── tsconfig.json
```

Mini 可以复用真正 transport-agnostic 的契约或纯逻辑，但不能为了“共享代码”重新引入 H5/Taro runtime coupling。当前 connectivity 首页只是原生链路验证，不是正式产品 UI；后续页面和视觉按 [`mini-product-ui-plan.md`](./mini-product-ui-plan.md) 分阶段实现。`local_dashboard/` 仍只是历史 fallback，不定义 Backend truth，后续可独立清理。

## 7. Extension rule

新增 Source、Projection、Store 或 Intelligence capability 时，先找到已有 extension point；不要重新建立并行 pipeline、第二套 storage model 或 compatibility namespace。稳定 data model 优先 composition，明确行为扩展点可以使用轻量 contract/abstraction。
