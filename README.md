# Family Spending Insights

Family Spending Insights 是一个本地运行的家庭收支系统。正式 Backend 采用 Canonical Modular Monolith：从不可丢失的 Source Evidence 建立稳定 SourceRecord / Transaction identity，在其上叠加 Mapping 与稀疏人工 Enrichment Decision，并确定性生成消费与现金流 Projection。Desktop Web 与 WeChat Mini Program 通过同一个 Application / HTTP API 使用这些能力。

## 架构

正式架构入口位于 `docs/architecture/`：

- `system-architecture.md`：长期稳定的 Domain、Application、Source、Persistence、Runtime 与 Interface 边界。
- `code-map.md`：当前正式 Canonical 代码结构与责任映射。
- `rebuild-strategy.md`：已完成的 Parallel Canonical Rebuild / Migration / Atomic Cutover 历史记录与不可回退原则。
- `mini-product-ui-plan.md`：正式微信小程序的产品范围、页面计划、UI 分阶段开发与验收标准。

Canonical Backend 当前位于：

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

正式代码不再保留 legacy `backend/`、旧 root-level pipeline/storage modules，也不保留 `rebuild/` compatibility workspace。

## 数据与隐私

根配置为 `family-spending.toml`，默认把 household data 放在本地 `data/`。整个 `data/` 都是用户持久化状态，不进入 Git；其中 Mapping 也属于 household reviewed knowledge，而不是 application checkout 的静态配置。

Canonical layout：

```text
data/
├── manifest.json
├── evidence/
│   ├── cmb-email/                 # 原始 EML，exact bytes
│   └── manual/records.jsonl       # Manual Source Evidence
├── state/
│   ├── identity/source-links.jsonl
│   ├── enrichment/decisions.jsonl
│   ├── mappings/
│   │   ├── merchants.yaml
│   │   └── categories.yaml
│   ├── schedules/
│   │   ├── rules.json
│   │   └── execution.json
│   └── feedback/feedback.jsonl
└── derived/
    ├── sources/
    ├── projections/
    └── indexes/
```

备份必须覆盖 Evidence、Durable Decisions、Reviewed Knowledge 与 Durable Product/Operational State。`derived/` 可以从上游状态重建，不能成为唯一事实源。

邮箱凭据只从环境变量读取：

```dotenv
EMAIL_ADDR=
EMAIL_AUTH_CODE=
```

不要把真实 `.env`、`data/`、Cutover backup 或 migration audit 提交到 Git。

## 环境准备

要求 Python 3.14+、uv、Node.js 与 npm：

```powershell
uv sync --frozen
npm install
Copy-Item .env.example .env
```

## Canonical CLI

从项目根目录执行：

```powershell
$env:PYTHONPATH = "src"
uv run --frozen python -m family_spending diagnose state
uv run --frozen python -m family_spending sync
uv run --frozen python -m family_spending rebuild projections
uv run --frozen python -m family_spending jobs run-due
```

启动正式本地 API：

```powershell
$env:PYTHONPATH = "src"
uv run --frozen python -m family_spending serve
```

默认监听 `127.0.0.1:8765`；`GET /api/health` 用于健康检查。

`sync` 只对尚未建立 durable identity 的新 SourceRecord 做 reconciliation；已有 SourceLink 不因 rebuild 或算法升级被重新猜测。CMB raw EML 是来源事实，legacy `transactions.csv` 不再是 Canonical truth。

## Desktop 本地开发 Runtime

统一启动 Backend 与 Desktop Web 开发 runtime：

```powershell
npm run dev
```

查看或停止：

```powershell
npm run dev:status
npm run dev:stop
```

微信小程序不再由这个 managed runtime 启动；它直接使用微信开发者工具。

## 微信原生小程序

正式 Mini 位于：

```text
frontend/apps/mini
```

这是可直接导入微信开发者工具的原生 TypeScript 小程序工程，`miniprogramRoot` 为 `miniprogram/`。不需要 Taro、不需要 H5 preview，也不需要先生成 `dist/`。

本地调试时先在独立终端启动默认 `127.0.0.1:8765` Backend，然后在微信开发者工具中导入 `frontend/apps/mini`。开发环境 Mini 直接通过 `wx.request` 读取 Canonical HTTP API；体验版/正式版在域名与部署完成前不会退回本地地址。

当前 Native Mini 已完成正式 Home、交易浏览与交易详情、Manual Input、轻量 Mapping Review，以及持久化多巴胺主题。首页、交易、记一笔和审核都直接使用 Canonical HTTP API；客户端不重建 Reconciliation、Mapping、Enrichment、Refund 或 Scheduling 业务规则。后续页面仍按 `docs/architecture/mini-product-ui-plan.md` 分阶段推进；开发工具说明和当前 Mini 验收状态见 `frontend/apps/mini/README.md`。

对会写入 household state 的 Backend mutation，仓库自动测试优先使用 `TemporaryDirectory` 隔离 household storage 和临时 HTTP Server，不应为了 UI 验收要求人工制造测试财务数据。真实 Mini 验收主要关注输入、导航、视觉和状态反馈；Mapping Review 的 Preview 可在真实 runtime 无痕验收，Apply 等持久化 mutation 默认由隔离 integration test 验证。确需正式 runtime smoke 时必须包含可验证的自动清理路径。

## 测试与构建

Backend：

```powershell
$env:PYTHONPATH = "src"
uv run --frozen python -m unittest discover -s tests -v
```

Frontend：

```powershell
npm run test:frontend
npm run typecheck:frontend
npm run build:web
```

Mini 的实际编译、预览与真机调试由微信开发者工具完成；仓库侧的 `test:mini` / `typecheck:mini` 负责纯 TypeScript/API contract 的快速回归。

架构 contract tests 会持续约束：Domain 不依赖 persistence/interface，Application 通过 ports 工作，HTTP 不形成第二条业务 pipeline，正式 source tree 不再导入已删除的 legacy Backend modules。

## 产品边界

- Desktop Web：完整工作台。
- WeChat Mini Program：微信原生轻量客户端，微信开发者工具是主要开发/验证环境。
- 不再维护 Mini H5/Taro 运行时。
- `local_dashboard/` 仍是历史 fallback；它不定义 Backend 架构或财务 truth，后续可在正式前端稳定后单独清理。

新增 Source、Projection 或 Store 时，应沿 `docs/architecture/system-architecture.md` 的 extension points 扩展，不恢复 legacy compatibility layer，也不让前端直接读取 household files。
