# Family Spending Insights

Family Spending Insights 是一个本地运行的家庭收支系统。正式 Backend 采用 Canonical Modular Monolith：从不可丢失的 Source Evidence 建立稳定 SourceRecord / Transaction identity，在其上叠加 Mapping 与稀疏人工 Enrichment Decision，并确定性生成消费与现金流 Projection。Desktop Web 与 WeChat Mini Program 通过同一个 Application / HTTP API 使用这些能力。

## 架构

正式架构入口位于 `docs/architecture/`：

- `system-architecture.md`：长期稳定的 Domain、Application、Source、Persistence、Runtime 与 Interface 边界。
- `code-map.md`：当前正式 Canonical 代码结构与责任映射。
- `rebuild-strategy.md`：已完成的 Parallel Canonical Rebuild / Migration / Atomic Cutover 历史记录与不可回退原则。

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

要求 Python 3.14+ 与 uv：

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

## 本地开发 Runtime

统一启动 Backend、Desktop Web 与 Mini H5 开发 runtime：

```powershell
npm run dev
```

查看或停止：

```powershell
npm run dev:status
npm run dev:stop
```

Mini H5 仅用于开发/测试；正式轻量客户端是 WeChat Mini Program。

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
npm run build:mini:h5
npm run build:mini:weapp
```

架构 contract tests 会持续约束：Domain 不依赖 persistence/interface，Application 通过 ports 工作，HTTP 不形成第二条业务 pipeline，正式 source tree 不再导入已删除的 legacy Backend modules。

## 产品边界

- Desktop Web：完整工作台。
- WeChat Mini Program：轻量 companion。
- Mini H5：开发/测试 runtime，不是第三个正式产品。
- `local_dashboard/` 仍是历史 fallback；它不定义 Backend 架构或财务 truth，后续可在正式前端稳定后单独清理。

新增 Source、Projection 或 Store 时，应沿 `docs/architecture/system-architecture.md` 的 extension points 扩展，不恢复 legacy compatibility layer，也不让前端直接读取 household files。
