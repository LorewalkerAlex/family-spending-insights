# Pre-Canonical Architecture Archive — 2026-08

本目录保存 Family Spending Insights 在 2026-08-15 Canonical Architecture 冻结之前的架构文档。

归档原因不是这些文档“错误”，而是它们记录了项目从 POC、领域模型建立、Backend Runtime consolidation、Frontend product migration 到当前已验证产品形态的**演进过程**。其中包含大量当时合理、但不再作为长期目标的实现约束。

归档文档：

- `family-consumption-data-architecture-design.md`
- `backend-technical-architecture-design.md`
- `frontend-product-architecture-design.md`

从本次归档开始：

- 新开发以 `docs/architecture/` 为权威；
- 归档文档只用于理解历史行为、迁移背景和旧设计理由；
- 不因为归档文档中的旧目录、类名、文件格式或 orchestration 方式而给新实现增加 compatibility debt；
- 当前真实业务行为仍以当前代码和测试为 reference，直到 Parallel Canonical Rebuild 完成 Atomic Cutover。
