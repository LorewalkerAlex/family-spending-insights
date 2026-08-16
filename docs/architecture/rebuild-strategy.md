# Family Spending Insights — Parallel Canonical Rebuild Record

> Status: **completed historical migration record**
> Strategy established: 2026-08-15
> Atomic production cutover: 2026-08-16
> Migration tooling historical commit: `a3ee0436078385d48d71039b3f32825f12742513`

## 1. Outcome

Family Spending Insights 已完成 Parallel Canonical Rebuild。旧 Backend 曾作为独立行为基线保留，新 Backend 在 `rebuild/` 中完成 Domain、Storage、Identity、Source Sync、Projection、Runtime、Application、HTTP/CLI、Migration 与 parity 验证，随后通过一次 Atomic Cutover 提升为正式 `src/family_spending/`。

正式工作树现在只有 Canonical Backend；`rebuild/` 与 legacy Backend 都不再是运行时组成部分。

## 2. Completed phases

```text
A. Domain + Storage Contract                 COMPLETE
B. Stable Source Identity + Evidence Stores COMPLETE
C. Durable Identity + Enrichment Decisions  COMPLETE
D. Generic Source Sync                      COMPLETE
E. Projections                              COMPLETE
F. Runtime / Coordinator / Supervisor       COMPLETE
G. Application + HTTP / CLI                 COMPLETE
H. Migration + Parity + Atomic Cutover      COMPLETE
```

Phase H 在真正切换前已经验证：

- Legacy → Canonical migration tooling
- Real household semantic parity
- storage migration dry-run
- restart / rebuild reproducibility
- API / CLI compatibility
- Desktop / Mini build compatibility
- successful disposable cutover rehearsal
- injected post-switch failure + exact rollback rehearsal

真正 Cutover 使用同一原则：freeze writes、独立 snapshot、staging migration、semantic gates、code/data switch、Canonical tests/CLI/HTTP gate，任何 gate 失败都在重新开放写入前恢复 old code + exact data snapshot。

## 3. One-time migration semantics

### CMB evidence and identity

- Raw EML exact bytes 是不可丢失 Source Evidence。
- Legacy `transactions.csv` 只作为 migration audit/reference，不进入 Canonical truth。
- Legacy `source_email + source_index` 被显式映射到 evidence-anchored Canonical SourceRecord identity。
- 已有 Transaction ID 与 Source relationship 被保留；普通 sync/rebuild 不重新猜 durable identity。

### Manual Source

- Manual source-native facts 保留为 Evidence。
- legacy merchant/category duplication 不进入 Manual Evidence。
- 真正用户决定迁移为 sparse `EnrichmentDecision`。

### Enrichment and Mapping

- 只持久化真实 merchant/category override 与 note。
- 等于 Mapping default 的 materialized merchant/category 不复制成 decision。
- Reviewed Mapping knowledge 进入 Canonical MappingStore，并在正式系统中属于私有 household state。

### Scheduled Input and Feedback

- legacy schedule mixed state 拆为 rules + execution state。
- 已生成 occurrence 继续是普通 Manual Evidence。
- Feedback identity/content/status/context 保留。

### Derived data

Legacy CSV、spending/financial report 与 indexes 都不是迁移 truth；Canonical Backend 从 Evidence + Durable Decisions + Reviewed Knowledge 确定性重建。

## 4. Atomic Cutover invariants

Cutover 使用以下不可妥协顺序：

```text
freeze writers
    ↓
independent full data backup
    ↓
validated migration staging
    ↓
canonical code + test promotion
    ↓
atomic-ish same-volume data switch
    ↓
canonical automated suite / CLI / HTTP gates
    ↓
remove rebuild workspace
    ↓
update docs and commit
```

在重新开放写入前，任一 gate 失败都必须：

```text
restore exact pre-cutover code
+
restore exact pre-cutover data snapshot
+
verify legacy backend can boot
```

不要在半迁移 household 上继续修复，不要建立双写、proxy、`legacy_backend`、`family_spending_v2` 或 Canonical Runtime 对 legacy schema 的兼容层。

## 5. Why migration tooling is absent from current Runtime

Legacy → Canonical migration 是一次性 release operation。工具与测试已由 Git 历史永久保存，因此完成 Atomic Cutover 后删除 `rebuild/` 是设计要求，而不是丢失能力。

如未来需要审计本次迁移，应查看 Git 历史中的 commit `a3ee0436078385d48d71039b3f32825f12742513` 及其前后阶段提交；不要为了潜在历史用途把 migration dependency 重新带回正式 Runtime。

## 6. Post-cutover verification policy

后续正常开发只验证 Canonical system：

- backend unit / integration / architecture contract tests
- CLI diagnose/sync/rebuild jobs
- HTTP API contract
- Desktop / WeChat build 与必要 UI behavior

不再运行 Legacy ↔ Canonical parity，也不再重复 Atomic Cutover rehearsal。人工 UI 验收只覆盖自动测试无法证明的 interaction；Mini H5 不作为正式产品 E2E gate。
