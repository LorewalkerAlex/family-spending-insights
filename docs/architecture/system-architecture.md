# Family Spending Insights — Canonical System Architecture

> Status: **canonical**
> Baseline: `main @ 21a3c88514d7241c6d3c787e70975a461afa0f5a`
> Scope: product system shape, backend domain boundaries, runtime model, persistence model, extension points, client boundaries, configuration, and long-term invariants.

## 1. Purpose

Family Spending Insights 已经完成核心业务流程验证：

- 定期从邮箱获取信用卡账单；
- 从原始账单重建交易事实；
- 通过可维护 Mapping 完成 Merchant / Category 解释；
- 支持 Manual Input 与 Scheduled Input；
- 支持 Mapping Review、Transaction Enrichment 和 Feedback；
- 生成消费与现金流 Projection；
- Desktop Web 与 Mini 通过统一 Application/API 消费后端能力。

因此本架构不再从“当前文件放在哪里”反推设计，而从正式上线后的产品形态定义长期稳定边界。当前代码只是迁移来源，不是目标架构的约束条件。正式迁移采用 **Parallel Canonical Rebuild**：旧 Backend 作为行为基线保持独立，新 Backend 按本架构并行重建并在完整 Parity 验证后一次性 Cutover。

## 2. Product Definition

Family Spending Insights 是一个**长期运行、可重复重建、以多来源财务事实为输入，以人工决策增强数据质量，并向多个客户端提供统一家庭财务视图的单机 Modular Monolith**。

它不是普通 CRUD 网站。核心职责只有四类：

```text
获取财务事实
    ↓
形成统一 Transaction identity
    ↓
通过 Mapping / 人工决策增强数据
    ↓
生成可查询、可展示、可导出的财务视图
```

### 2.1 正式客户端

- **Desktop Web**：完整家庭财务工作台。承担深度浏览、复杂审核、Mapping 管理、导入导出、批量操作、诊断和高级分析。
- **WeChat Mini Program**：轻量 companion。主要承担查看、简单 Manual Input、轻量 Review、快速确认 suggestion 与 Feedback。
- Mini H5 仅是开发/测试 runtime，不是第三个正式产品。

### 2.2 后端运行形态

正式部署只有一个权威 Backend Service：

```text
                    External Sources
                          │ Poll
                          ▼
                 Source Supervisor
                          │
                          │ new evidence
                          ▼
Desktop / Mini ──HTTP──► Mutation Coordinator ◄── Scheduler
                          │
                          ▼
                      Application
                          │
                          ▼
                    Financial Domain
                          │
                          ▼
                 Durable Filesystem
                          │
                          ▼
                     RuntimeState
                          │
                   Query / Suggestion
```

不引入 PostgreSQL、Redis、Kafka、微服务、Event Sourcing 或复杂 DI framework。当前数据量和单家庭部署不需要这些复杂度。

## 3. Architectural Layers

Canonical Backend 只有六类长期稳定责任：

```text
Interfaces
    ↓
Application
    ↓
Domain

Runtime ── coordinates Application + Sources + Persistence

Infrastructure / Persistence ── implements external I/O

Projections / Intelligence ── consume domain state, never own truth
```

### 3.1 Domain

Domain 只表达稳定业务概念和纯规则：

- SourceRecord
- Transaction
- SourceLink / identity semantics
- Reconciliation evidence and decisions
- Mapping semantics
- EnrichmentDecision / ResolvedEnrichment
- Refund / NetConsumption
- Scheduling rule semantics

Domain **不得依赖**：

- `Path`
- YAML / JSONL / CSV
- IMAP
- HTTP
- React / Taro
- 当前工作目录
- `data/` 的具体布局

### 3.2 Application

Application 负责 use case orchestration，而不是存储格式：

- Source Sync
- Manual Input create/correct/delete
- Transaction query / enrichment command
- Mapping Review
- Mapping import/export and management
- Scheduled Input
- Feedback
- Analytics query
- Transaction / Mapping export

Application 依赖 Domain 与 ports，不直接 `read_text()` / `write_text()` 某个业务文件。

### 3.3 Sources

每个 Source 是一个显式扩展点，包含：

```text
Source Module
├── Acquisition
├── Normalization
└── Reconciliation Policy
```

当前：

- CMB Email Source
- Manual Source

未来可以增加：

- Alipay Source
- WeChat Pay Source
- Other Bank Source

新增一个符合既有 Source 语义的来源时，正常情况只新增 Source module 并在 Composition Root 注册；不修改中央 Source Sync 流程。

### 3.4 Persistence / Infrastructure

文件系统是一个 Infrastructure implementation，而不是 Domain 的组成部分。

Application 依赖行为型 port，例如：

- EvidenceStore
- IdentityStore
- MappingStore
- EnrichmentDecisionStore
- ScheduleStore
- FeedbackStore
- ProjectionStore
- UnitOfWork

Canonical V1 使用 filesystem implementation。未来即使某个单独 store 改成 SQLite，也不应要求修改 Domain / Application。

### 3.5 Runtime

Runtime 负责“长期运行进程”的协调：

- RuntimeState lifecycle
- single-writer MutationCoordinator
- SourceSupervisor
- Scheduler trigger
- immutable snapshot publication
- Composition Root

Runtime 不重新定义业务规则。

### 3.6 Interfaces

HTTP / CLI 都只是 Application interface。

- HTTP 不做财务文件 I/O；
- CLI 不建立第二套业务 pipeline；
- Desktop 与 Mini 通过同一个正式 Application/API 访问能力。

## 4. Canonical Financial Data Flow

正式数据主链固定为：

```text
Source Evidence
      ↓
Source Normalization
      ↓
SourceRecord
      ↓
Reconciliation
      ↓
Transaction
      ↓
Enrichment Resolution
      ↓
Refund / Net Consumption
      ↓
Projection
      ↓
Application Query
```

Mapping 和 Enrichment Decision 从侧面进入 Enrichment：

```text
Mapping ───────────────────┐
                           ▼
Source + Transaction ──► Resolved Enrichment
                           ▲
Enrichment Decision ───────┘
```

稳定规则：

- Category 不参与 Transaction identity。
- Mapping 不参与 Transaction identity。
- Suggestion / NLP 不参与 Transaction identity。
- Income 与 Expense 共用 Transaction Core，但走不同 Enrichment 规则。
- Refund 是 Expense 语义，不建模成 Income。

## 5. Source Identity

### 5.1 Invariant

**SourceRecord ID 必须锚定 Source Evidence 本身，而不是锚定某一版 Parser 的输出顺序。**

Canonical Source identity 由下列概念形成：

```text
source_type
+
evidence_identity
+
stable_record_locator
```

如果同一 Evidence 中存在内容完全相同的多条记录，应使用 stable locator 或 deterministic duplicate ordinal 区分，而不是依赖“当前 parser 已识别出的第几个 transaction”。

### 5.2 CMB Email

Raw EML 是 CMB Source Evidence。Parser 可以升级，但同一原始账单行的 SourceRecord identity 必须保持稳定。

当前实现的 `source_email + source_index` 只作为 legacy migration input；Canonical schema 不继续以 parser 输出顺序作为长期 identity。

### 5.3 Manual Source

Manual Source Record 创建时产生永久 source id。更正属于显式 source lifecycle，不依赖 description / amount 推导新的身份。

### 5.4 Scheduled occurrence

Scheduled Input 继续通过稳定的 `rule_id + occurrence_date` 构造 occurrence identity，使重复运行可 recover，而不是生成重复事实。

## 6. Reconciliation

Canonical Reconciliation 分成：

- **Generic Engine**：候选建立、已有 identity reuse、歧义处理、evidence 记录、SourceLink invariant。
- **Source-specific Policy**：来源权威性、允许匹配的范围、source-specific decision rules。

例如当前 CMB 比 Manual Source 对同一信用卡财务事实具有更高 authority，因此 CMB 可以接管已有 Manual-backed Transaction 的 authoritative Source；这属于 CMB policy，而不是 Generic Engine 的硬编码名称判断。

已有 Durable SourceLink 默认被视为历史 identity decision。普通 rebuild 只对尚未建立 identity 的 Source 做 reconciliation。

## 7. Persistence State Model

持久化与运行状态分成五类。

### 7.1 Source Evidence

外部世界或用户直接提供、不能自动再造的事实：

- Raw EML
- Manual Source Records

必须备份。不能因为 rebuild 或算法升级被覆盖。

### 7.2 Durable Decision

已经做出的、未来算法不能擅自重新猜测的决定：

- SourceRecord ↔ Transaction SourceLink
- transaction-level Enrichment override
- user Note
- 未来显式 identity repair 结果

必须备份。

### 7.3 Configuration / Reviewed Knowledge

长期控制系统行为的用户配置：

- Merchant Mapping
- Category Mapping
- Scheduled Rules

Mapping 是家庭审核知识，不只是程序默认配置。

### 7.4 Durable Product / Operational State

- Feedback
- Schedule execution cursor

Schedule cursor 应持久化以提高效率，但 correctness 不能只依赖 cursor；稳定 occurrence identity 必须允许重新扫描和 recover。

### 7.5 Derived State

可从 Evidence + Durable Decision + Configuration 确定性重建：

- Parsed CMB records / diagnostic CSV
- Transaction view
- Resolved Enrichment
- Refund / NetConsumption
- Spending Projection
- Financial Projection
- indexes / suggestion caches

删除 derived 目录不应造成业务事实损失。

## 8. Transaction Persistence

Transaction 是从 SourceRecord + Durable SourceLink 重新建立的系统级领域状态。

Canonical V1 不增加独立 `transactions.json` 作为第二份 truth。

`transactions.csv` 从当前“CMB parser output / backend input”降级为 Derived Source Cache 或 inspection/export artifact：

```text
Raw EML
   ↓
CMB Parser
   ↓
SourceRecord
```

是 Canonical 主链。

## 9. SourceLink as Durable Identity Decision

SourceLink 保存：

```text
source_record_id
transaction_id
role = authoritative | supporting
```

它的语义是 identity history，而不是运行 cache。

因此：

- Full Rebuild 不重新生成已有 SourceLink；
- 新 Source 到达时可以增加/更新 relationship；
- 算法升级不会自动改变历史 Transaction identity；
- 真正错误的关系通过显式 repair use case 修改。

## 10. Enrichment Model

### 10.1 Durable Enrichment Decision

Canonical persistence 只保存用户真实表达过的稀疏决定：

```text
EnrichmentDecision
├── transaction_id
├── merchant_override?
├── category_override?
└── note?
```

没有 override 的字段不复制 Mapping 结果。

### 10.2 Resolved Enrichment

运行时根据：

```text
SourceRecord
+ Mapping
+ EnrichmentDecision
```

计算：

- merchant
- default category
- effective category
- category source
- display name
- review signals

Resolved Enrichment 是 Derived State。

### 10.3 Mapping propagation

当 Mapping 改变：

- 没有 override 的 Transaction 自动跟随；
- merchant/category override 保留；
- Note 永远不因 Mapping 变化丢失。

不再通过持久化完整 materialized Enrichment 来模拟这些规则。

## 11. Mapping Management

Mapping 是 Reviewed Knowledge：

```text
description → merchant
merchant → default category
```

正式运行的 active Mapping 属于 Household Data，与 application installation / Git checkout 解耦。

### 11.1 Review

Mapping Review 保持 deterministic：

```text
Unmapped description
    ↓
Preview impact
    ↓
User confirms
    ↓
Apply Mapping
```

### 11.2 Import / Export

Desktop 后续支持：

```text
Import
↓
Parse
↓
Validate
↓
Impact Plan
↓
Preview
↓
Confirm
↓
Commit
```

不能通过 UI 直接覆盖 YAML 文件。

Export 是 read-only Application capability。

## 12. Mapping Suggestion Intelligence

Suggestion 与 Decision 永久分离。

Canonical extension point：`MappingSuggestionEngine`。

可以逐步增加：

1. normalized exact/alias match；
2. token similarity；
3. fuzzy string matching；
4. alias rules；
5. small local NLP；
6. embedding / model-assisted candidate ranking。

输出只包含候选、confidence 与 evidence。Suggestion 不能直接修改 Mapping。

只有用户确认后进入 Mapping Review Apply，才形成 Durable Reviewed Knowledge。

Suggestion index 可以常驻内存并在 Mapping 更新后重建，不进入 backup truth。

## 13. Runtime Architecture

### 13.1 Two/Three Producers, One Mutation Pipeline

正式运行有三个 mutation producer：

```text
External Source Poll ─┐
                      ├──► MutationCoordinator
User HTTP Command ────┤
Scheduler Trigger ────┘
```

UI 不需要“检测输入变化”；HTTP command 本身就是事件。

### 13.2 Single Writer

所有改变财务状态的 use case 必须经过一个进程内 `MutationCoordinator`：

- Source Sync
- Manual Input
- Mapping Review / Import
- Enrichment update
- Scheduled occurrence
- Future Source import

同一时刻只有一个 authoritative mutation commit。

Feedback 可使用相同 coordinator 保持简单一致，也可以在明确证明独立后使用自己的小型 store lock；默认仍优先统一 single-writer model。

### 13.3 Immutable Snapshot Publication

读请求不进入 mutation queue。

```text
RuntimeSnapshot N
      │
      ├── queries continue reading N
      │
Mutation
      ↓
plan → persist → rebuild affected state
      ↓
RuntimeSnapshot N+1
      ↓
atomic swap
```

任何 query 都不应观察到半写入状态。

### 13.4 RuntimeState

RuntimeState 包含：

```text
RuntimeState
├── HouseholdSnapshot
├── QueryIndexes
├── SuggestionIndexes
└── OperationalState
```

HouseholdSnapshot：SourceRecords、Transactions、SourceLinks、Resolved Enrichment、Mappings、Projection references。

QueryIndexes：transaction by id/month/description/merchant、review indexes 等。

SuggestionIndexes：normalized descriptions、known descriptions per merchant、tokens、aliases 等。

OperationalState：last email poll、last successful sync、current mutation、queue depth、last background error、runtime generation。

所有 RuntimeState 都必须可以通过 persistent state 重建。

## 14. Source Supervisor

Backend service 长期运行时由 `SourceSupervisor` 定期触发外部 Source acquisition。

CMB Email：

```text
poll
↓
IMAP query
↓
no new evidence → finish
new EML → persist evidence
↓
MutationCoordinator
↓
Source Sync
```

Poll failure 不应终止 HTTP service。错误进入 OperationalState / logging，并等待下一轮 poll。

Email correctness 不依赖易碎的“最后一封邮件编号”；Raw Evidence identity 与幂等保存负责安全重复 polling。

## 15. Scheduled Input

Scheduled Input 不是第三种财务 Source；它是自动产生 Manual Source Evidence 的 orchestration mechanism。

Canonical 拆成：

- `ScheduledRule` — 用户 Configuration；
- `ScheduleExecutionState` — cursor / last processed occurrence；
- generated occurrence — 普通 Manual Source Evidence。

即使 execution cursor 落后，稳定 occurrence id 也必须允许重新扫描并 recover。

## 16. Projection Architecture

Projection 只消费当前领域状态，不反向改变 Source / Transaction / Enrichment。

Canonical modules：

- Spending Projection
- Financial Projection
- Month Coverage

未来新增 yearly summary、cashflow trend、merchant trend 等时，只新增 Projection / query 能力，不修改 Reconciliation。

Projection 持久化只是缓存/跨进程读取便利；可以从 durable state 重建。

## 17. Desktop and Mini Capability Boundary

只有一个 Backend / Application API。

| Capability | Desktop | Mini |
| --- | --- | --- |
| Overview | Full | Lightweight |
| Transaction read | Full | Yes |
| Manual Input | Full | Simple |
| Simple Review | Yes | Yes |
| Advanced Review | Yes | Optional |
| Mapping management | Yes | No |
| Mapping Import/Export | Yes | No |
| Transaction Export | Yes | No |
| Batch operations | Yes | No |
| Suggestion evidence/debug | Full | Simplified |
| Diagnostics | Yes | No |
| Feedback | Yes | Yes |

这些是 Presentation/Product Capability 差异，不产生两套 Backend 领域规则。

当前 `frontend/apps/web`、`frontend/apps/mini`、`frontend/packages/core` 的总体分层继续保留；本次 Backend Canonical Rebuild 不顺带重做前端。

## 18. Configuration

正式非敏感运行配置使用一个明确的 config root，例如：

`family-spending.toml`

概念结构：

```toml
[storage]
data_root = "./data"

[server]
host = "127.0.0.1"
port = 8765

[runtime]
email_poll_interval_seconds = 900

[sources.cmb_email]
enabled = true
host = "imap.163.com"
port = 993
mailbox = "招行信用卡"
subject_keyword = "招商银行信用卡电子账单"
```

Secrets 继续通过 environment：

- `EMAIL_ADDR`
- `EMAIL_AUTH_CODE`

`data_root` 是正式 Runtime Config。业务模块禁止写死 `Path("data/..." )`，也不依赖当前工作目录。

源码根目录不是 Runtime Config；`rebuild/src` → `src` 的切换由开发/打包工具控制，业务代码不知道自己处于 rebuild workspace。

## 19. Canonical Data Layout

目标逻辑布局：

```text
data/
├── manifest.json
├── evidence/
│   ├── cmb-email/
│   │   └── *.eml
│   └── manual/
│       └── records.jsonl
├── state/
│   ├── identity/
│   │   └── source-links.jsonl
│   ├── enrichment/
│   │   └── decisions.jsonl
│   ├── mappings/
│   │   ├── merchants.yaml
│   │   └── categories.yaml
│   ├── schedules/
│   │   ├── rules.json
│   │   └── execution.json
│   └── feedback/
│       └── feedback.jsonl
└── derived/
    ├── sources/
    ├── projections/
    └── indexes/
```

语义：

- `evidence/`：不能丢；
- `state/`：不能丢；
- `derived/`：可以删除后全部重建。

实际文件格式是 filesystem adapter 的实现细节；目录语义是 Canonical contract。

## 20. Storage Schema and Migration

Data root 必须包含 schema manifest，例如：

```text
storage_schema_version
created_at
last_migrated_at
```

规则：

- version == current：正常启动；
- version < current：要求显式 migration；
- version > current：拒绝由旧程序启动。

旧 schema compatibility 不永久留在 Domain / Application。Migration 是一次性转换任务，而不是运行时代码中的长期 `if legacy_field`。

## 21. Backup and Recovery Contract

真正需要备份：

```text
manifest.json
evidence/
state/
```

恢复流程：

```text
安装程序
↓
恢复 evidence + state
↓
执行必要 storage migration
↓
Full Rebuild derived state
↓
Runtime bootstrap
```

因此应用目录、derived cache 和 build artifact 不是 household backup 的必要组成部分。

## 22. Full Rebuild Contract

Full Rebuild 的输入固定为：

```text
Source Evidence
+ Durable Identity Decisions
+ Mapping / Configuration
+ Enrichment Decisions
+ other durable product state as needed
```

输出：

```text
SourceRecords
Transactions
Resolved Enrichment
Refund / NetConsumption
Projections
Runtime indexes
```

Full Rebuild **不得**：

- 重新猜已有 SourceLink；
- 删除用户 override / note；
- 把 suggestion 当作 decision；
- 修改 Source Evidence；
- 因 parser 顺序变化重编号已有 Source identity。

## 23. Target Package Shape

```text
src/family_spending/
├── domain/
│   ├── source.py
│   ├── transaction.py
│   ├── reconciliation.py
│   ├── enrichment.py
│   ├── mapping.py
│   ├── refund.py
│   └── scheduling.py
├── sources/
│   ├── cmb_email/
│   │   ├── connector.py
│   │   ├── parser.py
│   │   ├── adapter.py
│   │   └── reconciliation.py
│   └── manual/
│       ├── model.py
│       ├── adapter.py
│       └── reconciliation.py
├── application/
│   ├── ports/
│   ├── source_sync.py
│   ├── manual_input.py
│   ├── transactions.py
│   ├── mapping_review.py
│   ├── mapping_management.py
│   ├── enrichment.py
│   ├── scheduled_input.py
│   ├── feedback.py
│   ├── analytics.py
│   ├── export.py
│   └── queries.py
├── projections/
│   ├── spending.py
│   ├── financial.py
│   └── month_coverage.py
├── intelligence/
│   └── mapping_suggestions/
├── persistence/
│   └── filesystem/
│       ├── layout.py
│       ├── evidence_store.py
│       ├── identity_store.py
│       ├── mapping_store.py
│       ├── enrichment_store.py
│       ├── schedule_store.py
│       ├── feedback_store.py
│       ├── projection_store.py
│       └── unit_of_work.py
├── runtime/
│   ├── state.py
│   ├── coordinator.py
│   ├── supervisor.py
│   └── composition.py
├── interfaces/
│   ├── http/
│   └── cli/
└── config.py
```

目录表达责任边界，不以“每个文件越小越好”为目标。

## 24. Dependency Rules

长期必须保持：

```text
Domain
  ▲
  │
Application ◄── Projections / Intelligence contracts
  ▲
  │
Runtime / Interfaces

Persistence / Sources implement Application ports and depend inward.
```

禁止：

- Domain import filesystem / HTTP / source connector；
- Application import YAML/JSONL parser 作为业务步骤；
- Projection 修改 Durable State；
- Suggestion 修改 Mapping；
- UI 绕过 Application 直接操作 household files；
- Source module 修改不属于自己的 Evidence；
- 新 Source 通过修改中央 pipeline 名称分支接入。

## 25. Non-goals

Canonical V1 明确不为了架构完整感提前建设：

- 多用户 tenancy；
- 高并发写入；
- 分布式锁；
- 银行 Account / Asset / Liability 完整模型；
- Transfer 全量实现；
- Forecast 独立事实模型；
- 复杂 AI agent architecture；
- 自研 job scheduler framework。

这些只有在实际产品需求出现时才进入新的架构决策。

## 26. Architecture Invariants

开发与 Code Review 至少用下面的问题判断是否偏离架构：

1. 新 Source 是否可以主要通过新增 Source module + Composition 注册完成？
2. 更换某个文件格式是否可以限制在对应 Store implementation？
3. 用户决定是否和 Mapping 派生结果分开持久化？
4. 任何 durable mutation 是否通过 single-writer commit boundary？
5. Runtime 重启后是否仅凭持久化数据恢复？
6. 删除 `derived/` 是否不会丢失业务事实？
7. Suggestion/NLP 是否仍然只产生候选？
8. Desktop/Mini 是否仍然共享一个 Backend business truth？
9. Full Rebuild 是否保持已有 identity 与人工决定？
10. 新代码是否依赖 Canonical architecture，而不是历史 README/目录偶然形状？
