# Family Spending Insights Canonical Architecture

> Status: **authoritative architecture baseline**
> Baseline repository: `LorewalkerAlex/family-spending-insights`
> Baseline commit: `21a3c88514d7241c6d3c787e70975a461afa0f5a`
> Established: 2026-08-15

本目录定义 Family Spending Insights 在完成现有业务流程验证之后的正式目标架构。后续 Backend 重建、代码审查、目录设计、持久化设计和运行时设计均以这里的文档为准。

## 文档优先级

1. [`system-architecture.md`](./system-architecture.md) — Canonical System / Runtime / Persistence Architecture。
2. [`code-map.md`](./code-map.md) — 当前代码到 Canonical Architecture 的映射，以及每个模块的迁移状态。
3. [`rebuild-strategy.md`](./rebuild-strategy.md) — Parallel Canonical Rebuild、验证、数据迁移与 Atomic Cutover 策略。

当 README、历史设计文档、旧 Handoff 或当前实现与这里发生冲突时：

- **目标架构**以本目录为准；
- **当前已上线行为**以当前代码和自动化测试为准；
- **迁移期间的差异**以 `code-map.md` 的状态和 `rebuild-strategy.md` 的阶段定义为准。

## 历史文档

2026-08-15 之前用于推动当前实现演进的架构文档已归档到：

`docs/archive/architecture/2026-08-pre-canonical/`

归档文档保留历史决策和实现背景，但不再指导新的架构开发。不要为了兼容历史文档中的目录、类名、持久化格式或 orchestration 方式而污染 Canonical 实现。

## 核心约束摘要

- 单机、单进程、single-writer Modular Monolith。
- 文件持久化继续适用于当前数据量，不因“公司级架构”而引入数据库、消息队列或微服务。
- `Source Evidence → SourceRecord → Reconciliation → Transaction → Enrichment → Projection` 是稳定数据主链。
- Source、Persistence、Application、Runtime、Interface 必须有明确依赖边界。
- Source identity 必须锚定原始证据，而不是某版 parser 的输出顺序。
- SourceLink 是 Durable Identity Decision；普通 rebuild 不重新猜已有 identity。
- Enrichment 只持久化用户真实决定；Mapping 派生的 Resolved Enrichment 是可重建状态。
- Runtime 内存只保存可重建 Snapshot / Index / Operational State，不成为唯一业务事实源。
- Mapping suggestion / fuzzy match / NLP 只产生建议，不直接改变财务 truth。
- Desktop 与 Mini 共用一个 Application/API；差异属于产品 capability 和 presentation。
- Backend 采用独立 `rebuild/` 并行重建，最终一次 Atomic Cutover，不向正式代码引入过渡兼容层。
