# Family Spending Insights Canonical Architecture

> Status: **authoritative current architecture**
> Repository: `LorewalkerAlex/family-spending-insights`
> Established: 2026-08-15
> Canonical production cutover: 2026-08-16

本目录定义 Family Spending Insights 当前正式架构。Parallel Canonical Rebuild 已完成 Atomic Cutover；`src/family_spending/` 现在就是 Canonical Backend，而不是迁移目标。

## 文档优先级

1. [`system-architecture.md`](./system-architecture.md) — Canonical System / Runtime / Persistence Architecture。
2. [`code-map.md`](./code-map.md) — 当前正式 Canonical package 与责任映射。
3. [`rebuild-strategy.md`](./rebuild-strategy.md) — 已完成的 Parallel Rebuild、Migration 与 Atomic Cutover 历史记录，以及今后不得重新引入的过渡模式。

当 README、历史设计文档或旧 Handoff 与这里发生冲突时：

- **长期架构与 invariant** 以 `system-architecture.md` 为准；
- **当前代码布局** 以 `code-map.md` 与当前 source tree 为准；
- **迁移历史** 以 `rebuild-strategy.md` 与 Git 历史为准。

## 当前正式形态

```text
Interfaces ───────► Application ◄──── Runtime
                         │
                         ▼
                       Domain

Sources ──implements──► Application ports
Persistence ──────────► Application ports
Projections consume canonical state and remain rebuildable
```

正式 Backend：

```text
src/family_spending/
├── application/
├── domain/
├── interfaces/
├── persistence/
├── projections/
├── runtime/
├── sources/
└── config.py
```

`rebuild/`、legacy `backend/`、双写、proxy、v1→v2 compatibility layer 都不属于正式 Runtime。

## 核心约束摘要

- 单机、单进程、single-writer Modular Monolith。
- 文件持久化继续适用于当前数据量，不因“公司级架构”而引入数据库、消息队列或微服务。
- `Source Evidence → SourceRecord → Reconciliation → Transaction → Enrichment → Projection` 是稳定数据主链。
- Source identity 必须锚定原始证据，而不是某版 parser 的输出顺序。
- SourceLink 是 Durable Identity Decision；普通 rebuild 不重新猜已有 identity。
- Enrichment 只持久化用户真实决定；Mapping 派生结果属于可重建 resolved state。
- Mapping 属于 household reviewed knowledge，位于私有 data root，不随 Git checkout 发布。
- Runtime 内存只保存可重建 Snapshot / Index / Operational State，不成为唯一业务事实源。
- Suggestion / fuzzy match / NLP 只产生建议，不直接改变财务 truth。
- Desktop 与 Mini 共用一个 Application/API；Mini H5 仅是开发/测试 runtime。

## 历史文档

2026-08-15 之前用于推动旧实现演进的架构文档保留在：

`docs/archive/architecture/2026-08-pre-canonical/`

一次性 Migration tooling 与其测试保存在 Atomic Cutover 前的 Git 历史中；正式 Runtime 不因历史迁移需要而保留 legacy schema compatibility。
