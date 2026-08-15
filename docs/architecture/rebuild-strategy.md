# Family Spending Insights — Parallel Canonical Rebuild Strategy

> Status: **authoritative implementation strategy**
> Architecture: [`system-architecture.md`](./system-architecture.md)
> Mapping: [`code-map.md`](./code-map.md)
> Current production baseline: `main @ 21a3c88514d7241c6d3c787e70975a461afa0f5a`

## 1. Decision

本次 Backend 架构重构采用：

> **Parallel Canonical Rebuild with Atomic Cutover**

不在当前 `src/family_spending` 中边拆边修，不向 production path 引入临时 compatibility layer，不让新旧 Backend 互相 import。

旧 Backend 在整个重建期间保持可运行，作为 executable behavior reference。新 Backend 在独立 source root 中从第一行代码开始实现最终 Canonical contracts。只有全部 gate 通过后才一次性替换正式 Backend。

## 2. Why Parallel Rebuild

当前业务语义已经通过真实数据、自动测试、Desktop/Mini 纵向切片验证；主要风险来自架构迁移，而不是业务需求未知。

渐进式原地重构容易产生：

- v1/v2 facade；
- dual write；
- temporary adapter；
- legacy field compatibility；
- 新旧 persistence 同时存在；
- 中央 pipeline 继续背负历史条件分支。

这些会成为第二轮历史债务，因此本轮不采用。

## 3. Temporary Workspace

重建期间使用：

```text
rebuild/
├── src/
│   └── family_spending/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── parity/
├── fixtures/
├── migration/
├── parity/
└── rebuild.toml
```

### 3.1 Same package name

新代码始终使用正式 package name：

```python
from family_spending.domain.transaction import Transaction
```

禁止：

```python
from family_spending_v2...
from rebuild_backend...
```

开发阶段 source root 是 `rebuild/src`；Cutover 后 source root 是 `src`。业务 import 不需要修改。

### 3.2 Rebuild is not a production layer

`rebuild/` 只存在于迁移期间。Cutover 完成后整个 workspace 删除，不在正式仓库长期保留：

- `legacy/`
- `v2/`
- `compat/`
- `rebuild/`

等目录。

## 4. Hard Isolation Rules

重建期间必须遵守：

1. `rebuild/src/family_spending` 不 import 当前 `src/family_spending`。
2. 当前 `src/family_spending` 不 import rebuild code。
3. 不双写 current schema 和 canonical schema。
4. 不增加 production compatibility facade。
5. 不为了复用旧 parser/store 而违反 Canonical dependency direction。
6. 可以复制/重新实现纯业务算法，但必须通过新 Domain contract 和 tests 进入。
7. 当前 Backend 只在发现真实业务 bug 时修改；不能为了帮助新 Backend 迁移而增加过渡 API。

## 5. Rebuild Configuration

新 Backend 的 Runtime Config 与源码路径分离。

### 5.1 Runtime data root

`rebuild.toml` 指向独立 sandbox，例如：

```toml
[storage]
data_root = "./rebuild/.runtime/household"
```

新代码通过 `AppConfig -> StorageLayout` 获取所有路径，禁止硬编码 `Path("data/..." )`。

### 5.2 Source root

`rebuild/src` 由 test/package tooling 配置为 Python source root；业务程序不知道自己位于 rebuild workspace。

## 6. Private Household Data Safety

新 Backend 开发期间绝不直接写当前真实 `data/`。

真实数据只允许：

```text
current household data
        │ read/copy
        ▼
rebuild sandbox data root
```

所有 migration dry-run、parity 和 destructive test 都在 sandbox 中完成。

只有 Atomic Cutover 阶段才迁移正式 household root。

## 7. Implementation Phases

Phase 是 rebuild workspace 内部的实现顺序，不是 production 的渐进迁移。任何 Phase 完成前后，当前正式 Backend 都保持不变。

### Phase A — Canonical Foundation

实现：

- `config.py`
- Storage manifest/schema version
- `StorageLayout`
- Application ports
- Domain primitive models
- filesystem UnitOfWork contract
- core error/invariant strategy

重点 contract：

- Domain 不知道 Path / YAML / JSONL；
- data root 可替换；
- storage schema 显式；
- no legacy fields。

Gate：

- unit tests；
- tmp data root tests；
- forbidden-import checks；
- storage manifest compatibility tests。

### Phase B — Source Evidence and Stable Identity

实现：

- CMB raw evidence store / connector boundary；
- CMB EML parser；
- evidence-anchored stable SourceRecord identity；
- Manual Source model/store/adapter；
- SourceRegistry contracts。

不以 `transactions.csv` 作为主链输入。

Gate：

- 同一 EML 重复 parse 产生同一 Source ids；
- parser 增加识别能力时已有 raw record locator 的 identity 不漂移；
- Manual id 稳定；
- fake Source 可通过 SourceRegistry 接入而无需改 generic contracts。

### Phase C — Transaction Identity and Reconciliation

实现：

- Transaction core；
- Durable SourceLink；
- Generic Reconciliation Engine；
- CMB / Manual reconciliation policy；
- identity store。

Gate：

- 已有 SourceLink rerun 不重新猜 identity；
- CMB authority / Manual support behavior parity；
- ambiguity fail-fast；
- same-source rerun idempotency；
- new fake Source 不修改 central engine。

### Phase D — Mapping, Enrichment Decisions, Refund and Projections

实现：

- Mapping Domain + MappingStore；
- sparse `EnrichmentDecision`；
- `ResolvedEnrichment`；
- Review signals；
- refund/net consumption；
- month coverage；
- spending/financial projections。

Gate：

- Mapping change 自动传播到 non-overridden Transaction；
- overrides / notes 保留；
- Income bypasses Expense Mapping；
- refund parity；
- spending / financial semantic parity；
- derived outputs 可删后 rebuild。

### Phase E — Runtime

实现：

- immutable RuntimeState；
- QueryIndexes；
- MutationCoordinator；
- snapshot generation / atomic swap；
- SourceSupervisor；
- scheduler trigger；
- Composition Root。

Gate：

- concurrent read 在 mutation 期间始终看到完整旧 snapshot；
- successful commit 后一次切到新 snapshot；
- failed mutation 不发布半状态；
- restart only from persistent state succeeds；
- source poll failure 不终止 HTTP runtime。

### Phase F — Application Use Cases

原生实现：

- Source Sync
- Manual Input lifecycle
- Transaction queries
- Enrichment update
- Mapping Review
- Scheduled Input
- Feedback
- Analytics queries

Mapping import/export、Transaction export 的 architecture seam 在这里确定；若本轮只做 parity，可在 Cutover 后作为第一个正式新 feature 实现，不要求为迁移扩大范围。

Gate：

- use-case tests；
- transaction/mapping mutation UoW tests；
- current business behavior parity。

### Phase G — Interfaces

实现：

- HTTP transport
- CLI operator surface

新 HTTP 原生实现现有正式 frontend API contract，不通过旧 Backend proxy。

Gate：

- API contract tests；
- Desktop shared core contract compatibility；
- CLI 不建立第二条 business pipeline。

### Phase H — Migration, Parity and Product E2E

实现一次性 migration tooling，并在真实 household data 的 sandbox copy 上验证。

Gate 见后续章节。

## 8. Storage Migration Plan

Migration 只存在于 `rebuild/migration/`，最终 Runtime 不支持 legacy schema。

### 8.1 Raw EML

- 保持 exact bytes；
- 移入 canonical Evidence layout；
- 不重新下载作为 migration 前提。

### 8.2 CMB Source identity

Legacy CMB source id 基于 `source_email + source_index`。

Migration 必须建立：

```text
legacy source id
    ↓ correlate against current evidence / parsed record
canonical evidence-anchored source id
```

然后改写 SourceLink 的 `source_record_id`，**Transaction id 保持不变**。

迁移必须输出完整映射审计结果，并 fail on ambiguous/unmapped legacy source。

### 8.3 Manual Source

- 保留 source id 与 source-native facts；
- legacy merchant/category 字段不进入新 Evidence model；
- 必要的真实用户决策必须转移到 EnrichmentDecision。

### 8.4 Enrichment state

当前完整 materialized Enrichment 迁移为 sparse decision。

原则：

- `note != null` → preserve Note decision；
- explicit category override source → preserve category override；
- merchant 与当前 Mapping-derived merchant 不一致且代表显式用户选择 → preserve merchant override；
- 单纯等于 Mapping default 的 merchant/category 不写入 decision；
- income default 不作为 user decision 保存；
- migration 对无法无歧义判断的 legacy state fail-fast，而不是猜。

迁移完成后通过 ResolvedEnrichment semantic parity 验证。

### 8.5 Mapping

保持 Reviewed Knowledge 语义，迁入 canonical MappingStore/layout。

### 8.6 Scheduled Input

将当前混合结构拆成：

- ScheduledRule；
- ScheduleExecutionState。

已经生成的 occurrence 继续作为 Manual Evidence；cursor 迁移后必须能通过 stable occurrence id recover。

### 8.7 Feedback

保持 item identity/content/status/context，迁入 canonical durable product state。

### 8.8 Derived data

不迁移作为 truth：

- `transactions.csv`
- spending statistics
- financial summary
- indexes

由新 Backend 从 Canonical Evidence/State 全量重建。

## 9. Parity Strategy

Parity 是本轮验收核心，不要求内部文件 byte-for-byte 相同，而要求业务语义一致。

### 9.1 Reference execution

对同一份 input snapshot：

```text
Current Backend ──► Reference Results
New Backend ──────► Canonical Results
```

Current Backend 在此阶段是 executable specification，而不是 import dependency。

### 9.2 Must-match semantics

至少比较：

- Transaction identity；
- Source relationship / authority；
- transaction type/date/amount/currency；
- Merchant / Category / display；
- user overrides / notes；
- review qualification；
- refund matching；
- net consumption；
- month completeness；
- spending statistics；
- financial summary；
- Manual Input create/correct/delete behavior；
- Mapping Review preview/apply behavior；
- Scheduled Input due/recovery behavior；
- Feedback behavior；
- API response semantics consumed by current frontend。

### 9.3 Allowed structural differences

以下不要求 byte parity：

- legacy vs canonical SourceRecord id representation，在 migration map 明确且 Transaction identity/semantics 保持时；
- Enrichment materialized state vs sparse decisions；
- data directory layout；
- derived file serialization ordering，只要正式 contract 未要求顺序；
- internal class/module names。

## 10. Real Household Parity

最终 parity 必须使用真实 household data 的**只读复制件**。

建议流程：

```text
1. snapshot current private data
2. run current backend reference reports
3. migrate copy to canonical sandbox
4. full rebuild with new backend
5. compare semantic manifest
6. run mutation scenarios on disposable copies
```

Web Session 不需要读取私有数据内容；本地测试只回传 counters / PASS / targeted failure evidence。

## 11. Frontend Compatibility Validation

Backend rebuild 不修改正式 frontend architecture。

新 Backend 完成 HTTP contract 后验证：

- Desktop Overview
- Transactions
- Review
- Automation
- Feedback
- global Add Transaction
- Mini Overview
- Mini Transactions
- Mini Review
- Mini Add Transaction
- Mini Feedback

业务正确性由自动测试与 parity 证明；人工 E2E 证明正式产品真实可用。

## 12. Cutover Gate

只有全部满足才允许切换：

- Canonical unit/integration/contract tests pass；
- real-data semantic parity pass；
- storage migration dry-run pass；
- restart/rebuild reproducibility pass；
- no hard-coded household data paths；
- no new→old or old→new imports；
- no legacy schema branch in final Runtime；
- no dual-write / compatibility facade；
- Desktop API compatibility pass；
- Desktop product E2E pass；
- Mini H5 compatibility pass；
- WeChat production build pass；
- migration rollback/recovery procedure verified。

## 13. Atomic Cutover

Cutover 是一次独立、可回滚的 repository/data migration operation。

逻辑步骤：

```text
1. freeze current write activity
2. snapshot/backup current durable household data
3. run final legacy → canonical storage migration
4. verify migration manifest
5. replace src/family_spending with rebuild/src/family_spending
6. move canonical tests into formal test structure
7. switch build/test source root from rebuild/src to src
8. run complete automated suite
9. boot canonical runtime against migrated data
10. run targeted Desktop/Mini smoke
11. remove rebuild workspace
```

如果任何 gate 失败，在重新开放写入前恢复旧 code + old data snapshot。

## 14. No Transitional Production Code

最终 production tree 明确禁止留下：

- `family_spending_v2`
- `legacy_backend`
- runtime `if schema_v1`
- v1→v2 wrapper
- dual persistence
- compatibility proxy to old backend
- duplicate pipeline
- rebuild workspace

Migration history 由 Git 保存，不需要让运行时代码永久承担历史。

## 15. Documentation Cutover

Architecture docs 自本文件提交后立即成为目标架构 authority；当前 README 继续描述“当前可运行方式”，但不能覆盖 Canonical Architecture。

代码 Cutover 完成后：

- 更新 `code-map.md`，将 Current 与 Canonical 对齐；
- README 改写为正式运行/开发入口；
- rebuild strategy 可转入 archive，或保留为已完成 migration record；
- 历史 pre-canonical docs 继续留在 archive，仅供追溯。

## 16. First Implementation Slice

文档冻结后的第一个代码 Slice 应是 **Phase A — Canonical Foundation**，而不是先移动旧文件。

第一 Slice 只建立：

- rebuild workspace；
- package/tooling source root；
- Config；
- StorageLayout / manifest；
- Domain primitive contracts；
- ports；
- filesystem UoW contract；
- architectural import tests。

不接 HTTP，不改正式 Backend，不碰真实 household data。
