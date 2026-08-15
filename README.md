# Family Spending Insights

用于在本地获取、整理和分析家庭共同收支数据。

当前正式数据链路已经包含 CMB Email 与 Manual Source 两个 Source 入口；Scheduled Input V1 已作为调用 Manual Source 的月度编排能力接入同一领域骨架：

```text
CMB Email / Manual Input
→ Source Adapter / Source Record
→ source-aware Reconciliation
→ Transaction + Source Link
→ current Enrichment
   ├─ expense → Merchant Mapping / Mapping Review
   └─ income  → income_default / 其他收入（不使用 Merchant Mapping）
→ expense 退款归并生成 Net Consumption
→ spending_statistics.json schema v2
→ income + net spending → financial_summary.json schema v1
→ Application / local JSON API
   ├─ local_dashboard/（legacy fallback）
   ├─ Desktop Web
   └─ Mini H5 / WeChat

Scheduled Rule
→ 到期 occurrence
→ Manual Input / Manual Source
→ 上述同一 Pipeline
```

后端运行层已经从“各功能模块自行拼接文件读取 / Pipeline / rollback”收敛到明确的本地 modular runtime：

```text
CLI / HTTP
   ↓
FamilySpendingApplication
   ↓
BackendRuntime
├── CurrentHouseholdSnapshot
├── HouseholdPipeline
└── FileUnitOfWork
```

`BackendRuntime` 在进程内持有可重建的当前 household snapshot，Query 优先复用该 snapshot；Source Sync、downstream Projection rebuild、runtime-backed Enrichment / Mapping mutation 与 Scheduled due batch 通过显式 Pipeline / commit boundary 运行。具体技术边界见 `backend-technical-architecture-design.md`；领域事实与长期数据模型仍以 `family-consumption-data-architecture-design.md` 为准。

项目当前已经实现邮件获取、CMB Source Record / Transaction 身份分离、Manual Source 与跨来源 Reconciliation、Manual Input 查询 / 更正 / 删除生命周期、Scheduled Input 月度规则管理与幂等到期生成、支出侧 Merchant Mapping 与 Mapping Review / Mapping Correction、独立持久化的当前 Enrichment、退款归并、消费统计 Projection、收入 / 净消费 / 净现金流 Financial Summary Projection、本地 JSON Application/API，以及支持 source-native Manual Input 管理、Scheduled Input 管理、Mapping Review、家庭现金流概览、逐笔 Transaction 浏览和 transaction-only Enrichment exception 的本地 HTML Dashboard。跨端前端已经从基础 POC 进入 PC Web-first 稳定化阶段：Desktop Web 的 Overview、Transactions、Review、Automation、Feedback 五个正式 workspace 与全局 Add Transaction / Send Feedback 都已接入真实 Application/API；Overview 现在以选定完整月份为主上下文，展示月度结余 Hero、净消费趋势、Category 消费构成与 Top Merchant/display，并提供酸柠、莓果、橘浪、葡萄四套可切换的 Desktop 色调主题；消费分析继续通过正式 `GET /api/spending-statistics` 读取 schema v2，只消费后端已经聚合和对账的 Projection，不在 React 中重新计算消费事实。Transactions 提供桌面 master-detail、Expense transaction-only Merchant / Category / Note、Income Note-only 与 Manual Source 更正 / 删除，Review 提供 Mapping Review 聚合、Merchant 建议、Preview / Apply 与新 Merchant 二次确认，Automation 提供 Scheduled Input 创建、编辑、启停、删除与 Run Due。Mini 已真实接入 Overview、Transactions、Review、Add Transaction 与 Feedback；Automation 仍留在后续 Mini 收敛阶段。Desktop 与 Mini 继续共用 TypeScript contracts/service/view-model core，业务事实仍由既有 Python Domain / Application / API 负责，legacy `local_dashboard/` 继续作为功能 fallback。Taro WeChat production build 已通过，但真机联网、正式 AppID / HTTPS API 域名配置和公网部署仍不属于当前已验证范围。增长率/环比分析、收入分类体系、AI 报告以及面向公网部署与认证的远程 API 也仍未实现。

## 数据与隐私边界

```text
data/
├── emails/
│   └── *.eml
├── screenshots/
├── mappings/
│   ├── merchants.yaml
│   └── categories.yaml
├── transactions.csv
├── manual_source_records.jsonl
├── scheduled_input_rules.json
├── transaction_source_links.jsonl
├── enrichment_state.jsonl
├── feedback.jsonl
└── reports/
    ├── spending_statistics.json
    └── financial_summary.json
```

数据职责：
- `emails/`：从 163 邮箱保存的原始 RFC822 邮件，是不可变事实来源；稳定文件名中的账单日期也用于判断自然月消费数据是否完整。
- `screenshots/`：历史 Merchant Mapping 建立和识别验证使用的本地截图。
- `transactions.csv`：从全部原始邮件全量重建的 CMB 来源级事实数据；进入统一领域模型后对应 `SourceRecord`，不是系统级 Transaction 存储。
- `mappings/merchants.yaml`：人工确认的支出侧 `merchant_name → descriptions`。
- `mappings/categories.yaml`：人工确认的支出侧 `category → merchant_names`。
- `manual_source_records.jsonl`：Manual Source 的本地来源事实；当前录入保存 `type/date/amount/description/note`。Expense 的 Merchant / Category 由下游 Mapping / Enrichment 决定；Income 保留原始 description，但不进入 Merchant Mapping。更正会以新 Source Record 替换旧记录；删除最后一条 Manual Source 后文件会被移除，缺失文件表示空 Manual Source。Scheduled occurrence 生成后也只是普通 Manual Source Record。
- `scheduled_input_rules.json`：Scheduled Input V1 的本地编排状态，不是 Source 数据。保存月度规则及下次执行日期、启停状态和最近一次 occurrence 元数据；无规则时文件会被移除。
- `transaction_source_links.jsonl`：当前 Source Record → Transaction 关系。
- `enrichment_state.jsonl`：当前 Transaction Enrichment authoritative state；Expense 的 transaction-only Category exception 与 Income 的 `income_default` 当前状态都持久化在这里，并通过 `category_source` 区分来源。
- `feedback.jsonl`：本地产品 Feedback V1 状态，保存 `content/status/context` 等产品使用反馈；它不属于 Financial Transaction、Enrichment 或 Analytics，不参与任何财务 Projection。
- `reports/spending_statistics.json`：后端生成、可从正式状态重建的消费统计 Projection，继续使用 schema v2。
- `reports/financial_summary.json`：后端生成、可从正式状态重建的家庭财务摘要 Projection，schema v1；按月汇总收入、净消费与净现金流，不复制消费 category / merchant 明细。

除两份正式 Mapping 外，`data/` 中的原始邮件、截图、完整交易、运行态 Source/Link/Enrichment 状态、产品 Feedback、OCR 结果和派生 Projection 默认只保存在本地，不提交到 Git。

正式进入 Git 的数据文件只有：

```text
data/mappings/merchants.yaml
data/mappings/categories.yaml
```

`待分类` 是 Expense 的运行时和界面状态，不是正式 category，也不会写入 Mapping。Income 当前使用系统定义的 `其他收入` / `category_source = income_default`；它同样不写入 Expense Mapping 文件。

## 环境准备

项目要求 Python 3.14 或更高版本，并使用 uv 管理 Python 环境。

```powershell
uv sync
```

项目根目录的 npm workspace 同时管理 legacy Dashboard 的 Chart.js、新 Desktop Web、Taro Mini 与共享 frontend packages。项目使用 `.npmrc` 固定 `install-strategy=nested`，隔离 Desktop Vite 与 Taro 的依赖树并避免依赖偶然 hoist；一次根目录安装即可准备全部 JavaScript workspace：

```powershell
npm install
```

不要为了局部构建错误把安装策略改回全局 hoisted；Mini 需要的 Taro runtime / build dependency 应显式声明在自己的 workspace。

复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中填写邮箱账号和授权码：

```dotenv
EMAIL_ADDR=
EMAIL_AUTH_CODE=
```

邮箱目录、账单主题、查询起始日期和数据路径等非敏感配置统一定义在：

```text
src/family_spending/settings.py
```

## 获取原始邮件

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.ingestion.imap_163
```

该命令会：

- 登录 163 IMAP；
- 进入配置的招商银行信用卡邮件目录；
- 查找招商银行信用卡电子账单；
- 将完整原始邮件保存到 `data/emails/`；
- 跳过已经存在的邮件，避免重复下载完整内容。

原始 `.eml` 是后续所有交易数据的可追溯来源。账单文件名由邮件日期和内容哈希稳定生成；月份完整性只读取该稳定文件名中的日期，不解析 EML 正文账期。

## 重建交易事实

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.ingestion.cmb_email_transactions
```

该命令读取全部 `data/emails/*.eml`，成功解析所有账单后原子替换：

```text
data/transactions.csv
```

交易事实契约：

- 任意邮件解析失败都会停止重建，不写出部分结果；
- 信用卡还款记录不会作为消费交易写入；
- 银行描述保持原文，不自动拆分支付渠道或商户；
- 外观完全相同的交易不会被自动去重；
- 输出按交易日期和来源位置稳定排序；
- 原始金额方向为正数消费、负数退款；
- `transaction_id` 由来源邮件和邮件内位置稳定生成；在统一领域模型中它作为 CMB `SourceRecord.id`，不再直接充当系统级 Transaction ID。

CSV 字段为：

```text
transaction_id
transaction_date
amount
description
source_email
source_index
```

`read_transactions_csv()` 会严格校验 header、字段完整性、日期、金额、`source_index`、重复 ID、编码和可读性，并在错误中包含路径、行号和相关字段。

## CMB Source Record 与 Transaction

`CmbTransaction` 继续承担 CMB Email / CSV 边界的数据契约；进入正式主链后，`CmbSourceAdapter` 会把它无损转换为 `SourceRecord`。`CmbReconciler` 再建立独立的系统 Transaction 与 Source Link。

当前 Transaction Core 只保存：

```text
id
type
date
amount
currency
```

原始 description、`source_email`、`source_index`、Merchant 和 Category 都不复制进 Transaction Core。CMB 来源当前是对应信用卡财务事实的 authoritative Source；同一 CMB Source Record 重跑保持幂等。Manual Source 已通过 source-aware Reconciliation 与 CMB-backed / Manual-backed Transaction 做跨来源匹配。

## Merchant Mapping

正式 Mapping 与交易事实分开维护，并且当前只服务 **Expense 的 Merchant / Category 解释**。Income 不使用 Merchant Mapping，不会因为原始 description 未映射而进入 Mapping Review。

`load_merchant_mappings()` 读取两份正式配置并建立只读支出索引：

```text
description → merchant_name
merchant_name → default category
```

历史 `transaction_category_overrides.jsonl` 已完成一次性迁移并从正常 runtime 移除。transaction-only Category exception 现在只存在于 persistent Enrichment state；`transaction_override` 继续作为合法的 `category_source` 表达已迁移的历史决定，但 Mapping loader 不再读取或生成这类单笔事实。

新 Transaction 或缺失 Enrichment state 的正常初始化按 `type` 分流：

```text
expense
→ description 匹配 merchant
→ 获得 merchant 默认 category
→ 生成 merchant_default / unclassified Enrichment

income
→ 保留 Source description 作为展示证据
→ merchant_name = null
→ category = 其他收入
→ category_source = income_default
```

主要规则：

- Expense 未匹配 description 时，`merchant_name` 保持空值，`display_name` 使用原始 description，category 为运行态 `待分类`；
- Income 不读取 `description → merchant` Mapping，即使 description 文本碰巧与某个 Expense Mapping 相同，也仍保持 Merchant 为空并使用 `income_default`；
- Mapping 只定义稳定的 Expense Merchant 与默认 Category，不承载单笔 transaction exception；
- 已持久化的 `transaction_override` / `manual_override` 不会被普通 Mapping 重建静默覆盖；
- 升级前已经以隐式 `merchant_default` / `unclassified` 保存的 Income，会在全量统计重建时收敛到 `income_default`；显式历史 override 不会被迁移逻辑覆盖；
- 默认 Expense category 为 `其他支出` 时会产生非阻断复核信号；
- 默认 Expense category 为 `综合购物` 且净消费金额达到高额阈值时会产生非阻断复核信号；
- 复核信号只存在于运行结果中，不写回正式配置。

正常的未分类与 Mapping 错误审核已经收敛为 Mapping Review，而不是默认逐笔编辑 Transaction：

~~~text
unmapped Expense CMB / Manual description
→ 按 description 聚合 Review item
→ 选择已有 Merchant 或明确新建 Merchant
→ 确认 Merchant 默认 Category
→ Preview 影响范围
→ Apply Mapping + affected Enrichment
→ downstream Projections
~~~

Mapping Review 当前规则：
- 只有 Expense 进入 Review queue；Income 不使用 Merchant Mapping，因此不会产生 Mapping Review item；
- CMB 与 Manual Source 的未匹配 Expense description 共用同一 Review queue，并按 description 聚合交易笔数、金额和来源类型；
- Merchant 候选只用于提示，用户必须明确选择或新建，不会静默模糊合并；新 Merchant 需要显式二次确认；
- Preview 分别说明 `description → Merchant`、`Merchant → default Category` 的作用范围、被保留的 transaction-only 例外以及本次总影响 Enrichment 数量；
- Apply 只传播到仍然跟随旧 Mapping 的当前 Expense Enrichment state；已有显式 Merchant / Category 单笔例外不会被 Mapping Correction 静默覆盖；
- Preview token 绑定当前 Mapping 选择和受影响状态；预览后状态发生变化时 Apply 会拒绝旧 token，要求重新预览；
- Mapping、affected Enrichment 与两个派生 Projection 作为同一个 Application mutation 边界处理；失败时恢复命令前快照，避免留下半提交状态。

`transaction_resolution.py` 继续提供 `HouseholdPipeline` 使用的 household domain assembly 与纯计算 review helpers，但不再暴露第二个 operator CLI。需要只读检查当前 coherent backend state 时统一使用：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending diagnose state
```

## Manual Source 与跨来源 Reconciliation

当前已经实现第二个正式输入入口 Manual Source，用于补充非信用卡交易，或在信用卡账单到达前先记录交易。

最小输入：

~~~text
必填：
type
date
amount
description

可选：
note
~~~

`description` 是用户输入并持久化保存的 Manual Source 原始文本，不是 Canonical Merchant。Manual Input 不直接创建或修改 Merchant / Category。Expense 的已有 description 如果命中正式 Mapping，会沿用 `description → merchant → default category` 路径；新 Expense description 未命中时进入运行态 `待分类`，后续与 CMB 未匹配 Expense description 共用 Mapping Review。Income 同样保留原始 description，但不进入 Merchant Mapping 或 Mapping Review，当前直接使用系统默认 `其他收入`。

Manual Input 的正式写入口是统一 Application/API。Desktop Web、Mini 与本地 Dashboard 都通过 `POST /api/manual-inputs` 调用同一个 Application use case；不再保留平行的 feature-module 写入 CLI。录入框会从历史 Manual Source 中读取 distinct description，仅做去空白、大小写与前缀级别的轻量候选提示；用户可以复用已有 description，也可以明确新建，不会自动模糊合并。这个提示只处理 Source description 复用，不等于 Merchant Mapping。

Manual Input 会主动执行完整下游 Pipeline：

~~~text
Manual Input
→ Manual Source Adapter
→ Source Record
→ Reconciliation
→ Transaction
→ type-aware Enrichment
→ expense Refund / Net Consumption
→ spending_statistics.json schema v2
→ financial_summary.json schema v1
~~~

跨来源 Reconciliation 当前规则：
- Manual Source 创建新 Transaction 前，会检查已有 CMB-backed 与 Manual-backed Transaction。
- 找到唯一对应 Transaction 时复用现有 Transaction，不创建重复交易。
- 无匹配时才创建新的 Transaction。
- 多个候选且无法唯一判断时，拒绝该次 Manual Input。
- Category 完全不参与 Transaction identity 判断。
- Merchant 只作为辅助匹配证据；Income 默认没有 Merchant，因此不会为了匹配伪造 Merchant Mapping。
- CMB 后续到达并匹配 manual-only Transaction 时，复用同一个 Transaction identity，并由 CMB 成为该信用卡交易核心财务事实的 authoritative Source。
- Manual `description` 属于 Source Record 原始事实；Expense Merchant / Category 由共享 Mapping / Enrichment 路径决定，Income 使用非 Mapping 默认 Enrichment。Manual `note` 作为用户补充信息进入当前 Enrichment，但不进入 Transaction Core。
- Manual Input 在真正写入前先完成校验与 Reconciliation；写入 Manual Source、Source Link、Enrichment 或 Projection 任一步失败时，会恢复本次命令前的相关文件状态，避免正常故障路径留下半提交。

Manual Source 现在也支持显式查询、更正与删除，并保持 Source 生命周期与 Transaction / Enrichment 边界分离：
- 更正不是通用 Transaction Core PATCH，而是用一个**新的 Manual Source Record ID**替换旧 Source Record，然后重新执行 Reconciliation 与全部必要下游处理。
- 如果更正后的 Source Record 没有匹配到另一个既有 Transaction，系统把它视为同一真实交易的来源事实纠错，保留原系统 Transaction ID；已有 transaction-level Enrichment 也随同该 Transaction identity 保留。
- 在保留 Expense Transaction identity 时，如果当前 Merchant 仍跟随旧 description 的正式 Mapping，会按更正后的 description 重新应用 Mapping；显式 transaction-level Merchant 例外和 Category override 不会被静默覆盖。
- 如果更正后的 Source Record 唯一匹配到另一个既有 Transaction，则按正常 Reconciliation 复用目标 Transaction，不强行保留旧 manual-only Transaction identity，也不会把旧 Transaction 的 Merchant / Category 单笔例外静默复制到目标 Transaction。
- 更正命令显式提供 `note` 时会更新当前 Enrichment Note；Dashboard 编辑器展示并提交的是当前 Transaction Enrichment Note，避免旧 Source note 覆盖后来做过的 Note 修改。
- 删除只删除指定 Manual Source。若 Transaction 还有 CMB 或其他 Source 支撑，则 Transaction 保留；若它只由该 Manual Source 支撑，则重建后该 Transaction 与不再需要的 Enrichment / Source Link 一并退出当前状态。
- 创建、更正、删除都以 Manual Source、Source Link、Enrichment 与两个派生 Projection 为同一个 Application mutation 边界；失败时恢复命令前相关文件状态。

本地运行状态包括：

~~~text
data/manual_source_records.jsonl
data/transaction_source_links.jsonl
~~~

这些都属于本地运行数据，不提交到 Git。

## Scheduled Input V1

Scheduled Input 不是新的 Source。V1 保存的是“何时自动调用 Manual Source”的月度规则；每个到期 occurrence 都生成普通 Manual Source Record，并继续复用既有跨来源 Reconciliation、Transaction、type-aware Enrichment 与 Projection 主链。Expense occurrence 继续走 Merchant Mapping；Income occurrence 不进入消费 Mapping。

当前规则字段：

```text
id
enabled
type
amount
currency
description
note?
next_date
last_occurrence_date?
last_source_record_id?
last_transaction_id?
last_action?
```

V1 只支持固定金额的**每月一次**规则，并要求 `next_date` 落在每月 1–28 日，避免月底月份长度导致隐式漂移。每次成功处理一个 occurrence 后，`next_date` 前进一个自然月并保持相同日号。

执行语义：
- `FamilySpendingApplication.initialize()` 先通过 `BackendRuntime.bootstrap()` 同步已有 Source / Reconciliation / Projection，再执行截至本地当天所有启用且到期的规则；
- 也可以通过显式 `Run Due` 或 `python -m family_spending jobs run-due` 执行同一 runner；V1 不启动常驻线程、daemon 或系统级后台 Scheduler；
- 创建或编辑启用规则后，如果 `next_date` 已到期，会在同一 Application command 中立即处理到当前日期；暂停规则不会生成 occurrence；
- 如果程序一段时间未运行，启用规则会从保存的 `next_date` 开始逐月补齐到当前日期；不希望补齐时，应先把 `next_date` 改到未来再启用；
- 同一次 catch-up 会先汇总全部 due occurrence，再把新增 occurrence 作为普通 Manual Source candidates 交给一次 `HouseholdPipeline.plan_source_sync()`；不会为每个月份重复执行一整套 Source → Projection rebuild；
- 每个 `rule_id + occurrence_date` 会派生稳定的 Manual Source ID。重复 `Run Due` 不会重复记账；如果 Source state 已完整落盘但规则 cursor 尚未推进，下次执行可根据既有 Source Link 识别并恢复 occurrence；
- 编辑、暂停或删除 Scheduled Rule 只影响未来编排，不修改或删除已经生成的 Manual Source / Transaction 历史；历史 occurrence 如需纠错，继续使用 Manual Input 的 Source-level 更正 / 删除能力；
- 一次 due run 把规则、Manual Source、Source Link、Enrichment 与两个派生 Projection 纳入同一个 `FileUnitOfWork`。可捕获的执行失败会恢复命令前状态，避免多月补执行只成功一部分。

规则文件位于：

```text
data/scheduled_input_rules.json
```

它属于本地运行态数据，继续受 `data/*` 的默认 Git ignore 保护。

## Enrichment Application / API

Enrichment 当前是独立持久化的 authoritative current state，保存在：

```text
data/enrichment_state.jsonl
```

Expense 的 Mapping / Merchant default 负责新 Transaction 或缺失状态的初始化；Income 使用 `income_default / 其他收入`，不要求 Merchant。历史 transaction-level override 已迁入同一 persistent Enrichment state。普通统计重建会保留已经存在的显式 `transaction_override`、`manual_override` 和其他 Enrichment 编辑；升级前属于隐式 Mapping / unclassified 的旧 Income state 会规范化为 `income_default`。Transaction Core 仍不包含 Merchant、Category 或 Note。

本地 Application 提供：
- 创建 source-native Manual Input，并复用现有 Manual Source / Cross-source Reconciliation / downstream Pipeline；
- 查询当前 Manual Inputs 及其 Source role、关联 Transaction 与当前 Enrichment；
- 更正 Manual Source：替换 Source identity、重新 Reconciliation，并按实际匹配结果保留原 Transaction identity 或收敛到另一个既有 Transaction；
- 删除 Manual Source，并根据是否仍有其他 Source 支撑决定 Transaction 是否保留；
- 查询历史 Manual description，供录入时做轻量复用提示；
- 创建、查询、编辑、启停和删除 Scheduled Input 月度规则，并显式执行到期规则；
- Scheduled occurrence 通过 Manual Source 主链落地，不建立第二套 Transaction / Enrichment 规则；
- 查询当前 Transaction + Source identity + Enrichment；
- 查询正式 Expense Category 列表；
- 查询按未匹配 Expense description 聚合的 Mapping Review workspace；
- 预览 `description → Merchant` 与 `Merchant → default Category` 修改的准确影响范围；
- 应用经过预览确认的 Mapping Correction，并只更新仍跟随 Mapping 的当前 Expense Enrichment；
- 修改 Expense 单笔 Transaction 的 Merchant、Category、Note，作为 transaction-only Enrichment exception；
- Income 的 Merchant / 消费 Category 不通过该界面修改；当前只允许修改 Note；
- Enrichment / Mapping 修改后只继续执行 Refund / Net Consumption / Analytics / 两个 Projection；
- 不因为 Enrichment 编辑重新执行 Source Adapter、Reconciliation 或 Transaction identity 构建。

对 Expense，`category = null` 表示清除显式 Category，并恢复当前 Merchant 的默认分类；如果当前 Merchant 没有默认分类，则回到运行态 `待分类`。Income 的默认分类不使用这一消费 Mapping reset 语义。

启动统一后端 JSON API：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending serve
```

`serve` 默认监听 `127.0.0.1:8765`。这是本地 JSON API 的唯一 operator transport 入口；跨端前端开发默认使用后文的 managed runtime，并不要求固定占用 8765。当前端点包括：

```text
GET   /api/health
GET   /api/financial-summary
GET   /api/spending-statistics
GET   /api/feedback
GET   /api/categories
GET   /api/manual-descriptions
GET   /api/manual-inputs
GET   /api/scheduled-inputs
GET   /api/mapping-reviews
GET   /api/transactions
GET   /api/transactions/{transaction_id}
POST  /api/feedback
PATCH /api/feedback/{feedback_id}
POST  /api/manual-inputs
POST  /api/manual-inputs/{source_record_id}/corrections
DELETE /api/manual-inputs/{source_record_id}
POST  /api/scheduled-inputs
PATCH /api/scheduled-inputs/{rule_id}
DELETE /api/scheduled-inputs/{rule_id}
POST  /api/scheduled-inputs/run-due
POST  /api/mapping-reviews/preview
POST  /api/mapping-reviews/apply
PATCH /api/transactions/{transaction_id}/enrichment
```

`GET /api/financial-summary` 与 `GET /api/spending-statistics` 都只读取当前已经生成的 Projection，不在 GET 后隐藏 Source Sync、Projection rebuild 或其他 mutation。Spending Statistics 读取通过 `FamilySpendingApplication` 的 Query 边界和 `backend/projection_queries.py` 完成，HTTP transport 不直接承担财务文件读取；缺失或不支持的 Projection 会作为当前状态错误返回。Feedback API 只维护本地产品反馈，不触发 Financial Transaction / Enrichment / Projection Pipeline。

统一 `serve` 入口创建 `FamilySpendingApplication`。初始化时先执行 `BackendRuntime.bootstrap()`：运行一次完整 Source Sync、发布 `CurrentHouseholdSnapshot`，再执行截至当天的 Scheduled Input due occurrences。正常 Query 从 runtime snapshot 读取；如果受跟踪的 Source / Link / Enrichment / Mapping 文件被外部修改，runtime 会先检测 filesystem fingerprint 并重新装载已 reconciled current state，而不是在每个 GET 中重复跑 Reconciliation。若外部 Source 变化已经使持久化 links 失效，则 refresh 会明确报错并要求重新执行 Source Sync。

Runtime-backed mutation 保持 authoritative state 与可重建 Projection 的提交边界。Manual Input create/correct/delete 通过 `ManualInputCommandService` 进入统一 Runtime / Source Sync / `FileUnitOfWork` 路径；Enrichment PATCH 与 Mapping Review Apply 使用共享 `FileUnitOfWork` 协调 Enrichment / Mapping 与两个 Projection；Scheduled due batch 把规则、Manual Source 和 downstream state 纳入同一 commit boundary。所有产品写路径都只经过这套正式 Application / Runtime 编排，不再维护第二套 feature-level orchestration。

## 退款归并

正式消费统计在 Source Record → Transaction → Enrichment 建立后调用 `reconcile_refunds()`。退款匹配只处理 Expense；Income 不进入退款归并。退款匹配会读取 authoritative Source Record 的 description，并可使用当前 Merchant identity 作为辅助证据；Category 和 transaction override 不参与退款身份判断。

Expense 原始金额方向：

```text
amount > 0：消费
amount < 0：退款 / reversal
amount = 0：忽略并单独计数
```

匹配顺序：

1. 在历史同 description 消费中匹配当前剩余金额完全相同的最近一笔；
2. 若未命中，并且双方 description 已映射到同一 merchant，则在过去 30 个自然日内匹配同额的最近一笔消费；
3. 若仍未命中，再按同 description 历史消费从近到远累计扣减；
4. 无法匹配的剩余退款不进入统计，只记录数量和金额摘要。

退款只能抵消历史消费，不能抵消未来交易。Merchant 回退只使用当前 Merchant identity，不使用 Category 或 transaction override。

退款归并不会改写 Transaction Core。原始消费保持正数、退款保持负数；下游得到独立的 `NetConsumption(transaction_id, spending)` 派生结果，其中 `spending` 为正的剩余净消费金额。部分退款仍引用原消费 Transaction；完全退款的 Transaction 和 Source Record 继续存在于正式领域状态，但不会产生 NetConsumption，也不进入消费统计笔数。

## 同步当前后端状态与重建 Projection

完整 Source Sync 的正式 operator 入口为：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending sync
```

完整后端链路为：

```text
BackendRuntime.sync_sources()
→ HouseholdPipeline.plan_source_sync()
   → read CMB transactions + Manual Source records
   → read existing Source Links + current Enrichment state
   → load_merchant_mappings()
   → build_household_domain_state()
      → CMB / Manual Adapter + Source Record
      → source-aware Reconciliation / Transaction / Source Link
      → preserve or initialize type-aware current Enrichment
   → normalize legacy implicit Income Enrichment when needed
   → build_spending_projection()
      → reconcile_refunds() → NetConsumption
      → aggregate_spending()
      → load_month_coverage()
      → spending schema v2 + financial summary schema v1
→ FileUnitOfWork
   → persist Source Links / Enrichment / both Projections
→ publish refreshed CurrentHouseholdSnapshot
```

完整 Source Sync 只通过上述统一 operator 入口执行；不再保留平行的统计生成 CLI。

只需要从已经 reconciled 的 current state 重建派生输出时，可以跳过 Source Adapter / Reconciliation：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending rebuild projections
```

只读检查当前 coherent state：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending diagnose state
```

当前数据规模仍不引入数据库、持久化 runtime cache 或增量统计状态；`BackendRuntime` 的内存 snapshot 是可从正式文件状态重建的进程内 read model。

`spending_statistics.json` 继续只表示 Expense 净消费，并保持原有 schema v2：

- 全部自然月的净消费金额、交易笔数和月份数；
- 当前展示月份的净消费金额、交易笔数和月份数；
- 月份汇总；
- 月份 × category；
- 月份 × merchant/display。

消费统计规则：

- 未退款消费计 1 笔；
- 部分退款后的消费仍计 1 笔；
- 完全退款、无法匹配的剩余退款和零金额交易不计入；
- 待分类净消费仍进入总额、category 和 merchant/display 对账；
- 待分类 merchant 使用原始 description 作为展示名，但不会成为正式 `merchant_name`；
- 每月总金额和笔数必须分别等于 category 与 merchant/display 汇总之和。

`financial_summary.json` 使用独立 schema v1，并按 Transaction 月份合并两个方向：

```text
Income Transaction amount
             ↓
       total_income

Expense → Refund / NetConsumption
             ↓
      total_spending

net_cash_flow = total_income - total_spending
```

它包含 `all_data` / `shown_data` 汇总，以及逐月：

- `total_income_minor` / `income_transaction_count`；
- `total_spending_minor` / `spending_transaction_count`；
- 可为负数的 `net_cash_flow_minor`；
- `spending_data_complete`；
- `show`。

Income 当前要求金额为正数；退款仍是负金额 Expense，不会被错误统计为收入。Financial Summary 不按 Income category 细分，也不建立 Income Merchant 汇总。

### 自然月完整性与展示策略

信用卡账单约按每月 10 日切分，因此单份账单不能证明一个完整自然月。对于自然月 `M`，后端要求同时存在：

```text
M 月 10 日账单
M+1 月 10 日账单
```

例如 `2026-06` 需要同时存在 `2026-06-10_<hash>.eml` 和 `2026-07-10_<hash>.eml`。

消费 Projection 每个月份包含两个独立布尔字段：

- `is_complete`：由 CMB 原始账单文件覆盖推导出的消费侧事实；
- `show`：当前产品展示策略。

当前策略是 `show = is_complete`，但公共契约不把两者合并。后端始终保留完整和不完整的全部消费月份，不在生成阶段删除月份。

Financial Summary 对同一覆盖事实使用更明确的字段名 `spending_data_complete`，避免把 CMB 信用卡账单覆盖误解为“收入数据完整”。当前 `financial_summary.json` 的 `show` 同样沿用消费侧覆盖策略，因此 `shown_data` 表示“消费侧账单覆盖完整的月份中的家庭财务摘要”；它**不证明 Income Source 已覆盖该月全部收入**。未来如果增加收入来源完整性事实，应单独建模，不复用 CMB 消费完整性。

`spending_statistics.json` 金额和 `financial_summary.json` 的 Income / Spending 金额均使用人民币最小单位“分”的安全整数表示；Financial Summary 的净现金流允许负安全整数。两个文件都保持确定性字段顺序和原子替换，不包含逐笔来源邮件或退款分配历史。

## 跨端前端

首个跨端前端 POC 已完成，Transactions 纵向迁移、PC Web workspace 稳定化批次以及当前 Spending Analytics 纵向切片都已经进入同一 PC Web-first 主线：

```text
frontend/
├── apps/
│   ├── web/          # Desktop React / Vite
│   └── mini/         # Taro React → H5 preview / WeChat
└── packages/
    ├── core/         # Zod contracts, services, view models, formatting
    └── design-tokens/
```

当前迁移范围：

- Desktop Web：Overview、Transactions、Review、Automation、Feedback 五个正式 workspace 与全局 Add Transaction / Send Feedback 均已接真实 Application/API；当前已经不存在顶层 migration-state workspace；
- Overview：保留 Financial Hero 与近期月份表，并通过 shared presentation transform 展示最近最多 12 个后端 `show=true` 自然月的收入 / 净消费趋势；同时通过 `GET /api/spending-statistics` 读取后端 schema v2，只展示 `show=true` 月份的净消费总额/笔数、Category 构成与排行、Top Merchant/display 和待分类状态；前端只计算展示占比，不重算月份完整性、退款归并、Category/Merchant 聚合或财务事实；
- Transactions：使用桌面 master-detail，支持月份筛选、Expense transaction-only Merchant / Category / Note、Income Note-only 语义，以及 Manual Source 更正 / 删除；Add Transaction 继续只创建 source-native Manual Source；
- Review：使用桌面 master-detail 展示按 Expense description 聚合的待审核项，提供 Merchant 建议 / 已有 Merchant 复用、默认 Category、Preview 影响范围、Preview 失效保护、Apply 和新 Merchant 二次确认；Mapping propagation / token / rollback 仍完全由后端负责；
- Automation：使用 List + Editor 管理 Scheduled Input，支持创建、编辑未来规则、启停、删除和显式 Run Due；前端不实现 recurrence、due 判断、幂等、恢复或 Reconciliation；
- Mini：Overview、Transactions、Review 与 Feedback 已接真实 Application/API；Transactions 使用触屏列表 → Detail，Review 使用列表 → Review Detail，Add Transaction 使用独立页面；Automation 暂留在 More 中等待 Mini 专门收敛阶段；
- Desktop 与 Mini 共用 Financial Summary / Spending Statistics / Feedback / Transaction / Manual Input / Mapping Review / Scheduled Input 的 schema、service、view-model 与格式化语义，但分别使用 BrowserTransport 与 TaroTransport，不共享平台 UI；
- 当前产品开发优先把 PC Web 做到稳定可用。只要 backend/shared contract 未改变，不再为同一业务语义重复执行 Desktop + Mini 人工 E2E；Mini 继续通过类型检查与 H5 / WeChat build 防止集成断裂，并在进入 Mini 专门阶段后做集中人工验收；
- 业务正确性优先由隔离自动测试证明：Python Application/API 使用临时目录和 fixture 验证 Mapping Review / Scheduled Input 等状态变化，shared frontend 使用 mock transport / schema / presentation 单元测试验证请求与展示语义；人工 E2E 主要承担阶段性的真实浏览器可用性、布局与交互 smoke；
- Mini H5 仅作为桌面浏览器中的开发预览，使用居中的 phone-sized viewport 和 H5-only typography/layout 校准，不是第三个正式产品；Taro dev server 显式关闭自动打开浏览器；
- Transactions 真实本地数据 create/delete smoke、Review Desktop Preview smoke 与当前五 workspace PC Web 组合 smoke 均已完成；临时 Review fixture 在测试后从快照恢复，不保留测试 Mapping / Transaction 数据；
- WeChat production build 已验证通过；真实 WeChat runtime 仍需要有效的 Mini Program 配置与可访问的 HTTPS API origin，当前实现不声称已经完成真机联网或正式发布。

### Managed local development runtime

正常跨端开发统一从项目根目录使用：

```powershell
npm run dev
```

managed runtime 同时管理 API、Desktop 和 Mini H5。API worker 统一执行 `python -m family_spending serve`，因此本地产品运行与独立 operator CLI 使用同一 `BackendRuntime` / `HouseholdPipeline` 入口。它把当前实例记录在受 Git ignore 保护的 `.runtime/dev.json`，再次执行 `npm run dev` 会复用同一 runtime / PID / port，不会不断创建新的端口服务，也不会自动打开浏览器。

常用命令：

```powershell
npm run dev
npm run dev:status
npm run dev:stop
npm run dev:restart
```

默认 preferred ports 为：

```text
API      18765
Desktop  15173
Mini H5  11087
```

如果 preferred port 已被其他项目占用，只会为本次 managed runtime 从该起点向后选择空闲端口，不会按端口杀其他进程。可通过以下环境变量覆盖 preferred port：

```text
FAMILY_SPENDING_API_PORT
FAMILY_SPENDING_WEB_PORT
FAMILY_SPENDING_MINI_H5_PORT
```

Mini WeChat runtime 的远程 API origin 使用 `TARO_APP_API_BASE_URL` 在 Taro build config 中编译进入客户端；H5 开发预览继续通过同源 `/api` proxy 访问 managed API。

### Cross-platform frontend checks

```powershell
npm run test:dev-runtime
npm run typecheck:frontend
npm run test:frontend
npm run build:web
npm run build:mini:h5
npm run build:mini:weapp
```

验证按责任层拆分，而不是把所有正确性都交给人工 E2E：

- Domain / Application / HTTP 业务行为使用临时目录、fixture Source / Mapping 与本地测试 Server 自动验证，不触碰真实家庭数据；
- shared frontend 自动验证严格 schema decoding、service method/path/body、纯 presentation transform 与错误边界；
- Desktop / Mini 的 typecheck 和 production build 用于发现平台集成断裂；
- 人工浏览器验收按阶段合并执行，只证明页面在真实运行环境中可用、布局合理、交互顺畅，不重复证明已经由同一 backend/shared core 自动覆盖的业务语义；
- 当前阶段以 PC Web 为主，Mini 的重复人工验收延后到 Mini 专门收敛阶段；只有修改了 Mini 平台 UI、shared contract 或底层业务语义时，才按风险补对应验证。

`local_dashboard/` 在迁移完成前继续保留，并与上述新前端共享同一后端事实而不是被 iframe 或半迁移进新 shell。

## 本地消费统计 Dashboard

先安装一次本地图表依赖：

```powershell
npm install
```

Dashboard 现在同时消费两个静态 Projection 与本地 Application/API。需要在两个终端分别启动：

```powershell
# 终端 1：初始化 BackendRuntime 并提供 Transaction / Enrichment API
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending serve
```

```powershell
# 终端 2：从项目根目录提供静态文件和 reports JSON
uv run --frozen python -m http.server 8000
```

浏览器访问：

```text
http://localhost:8000/local_dashboard/
```

聚合统计直接读取：

```text
/data/reports/spending_statistics.json
/data/reports/financial_summary.json
```

Manual Input、Scheduled Input、Mapping Review 与逐笔 Transaction / Enrichment 都通过本地 API `http://127.0.0.1:8765/api` 读取和修改。API 不可用时，已经生成的静态 Projection 仍可独立展示；Application workspace 会分别显示连接错误。

当前能力：
- 原有消费总览继续使用 `spending_statistics.json` 的 `summary.shown_data`，不会把 `show=false` 月份金额混入当前消费展示；
- 新增家庭现金流概览，独立读取 `financial_summary.json` schema v1，展示收入、净消费、净现金流和按月汇总；
- Financial Summary 的月份 selector 使用 sidecar 自己的 `show=true` 月份，并明确显示“消费数据覆盖完整”语义；
- 原消费月份选择器只列出 `show=true` 月份，并保留全部符合展示策略的月份；
- 展示后端已经排序的 category 和 merchant/display 汇总；
- 显示待分类 Expense 且不遗漏金额；
- Manual Input 表单支持录入 `type / date / amount / description` 以及可选 `note`；description 先作为 Manual Source 原始事实保存，输入时只从历史 Manual description 提供轻量复用候选；
- Expense 新 description 可进入 Mapping Review；Income 不进入 Mapping Review，而是显示原始 description + `其他收入`；
- Manual Input 管理区列出当前 Manual Source、其 authoritative/supporting role、关联 Transaction identity 与当前 Enrichment，并可选中现有记录执行 Source-level 更正或删除；
- 更正会明确生成新的 Manual Source ID；普通 manual-only 事实纠错在未匹配其他 Transaction 时保留原 Transaction ID，而真正匹配到已有 Transaction 时按 Reconciliation 收敛到该 Transaction；
- 更正编辑器使用当前 Transaction Enrichment Note；Expense Merchant / Category 的稳定 Mapping 修改继续走 Mapping Review，transaction-only Expense Merchant / Category 例外继续走 Transaction Workspace，不混入 Source correction；
- 删除 Manual Source 后，如果没有其他 Source 支撑原 Transaction，则该 Transaction 从当前领域状态移除；若仍有 CMB 等来源支撑，则 Transaction 保留；
- Manual Input 创建、更正或删除成功后由后端完成 Reconciliation 与两个 Projection 刷新，Dashboard 再重新读取相关 workspace 与静态统计；
- Scheduled Input 区可以创建月度规则、查看下一执行日期和最近 occurrence、编辑未来规则、启停、删除以及显式 Run Due；界面只提交规则命令，不复制到期判断、幂等或 Reconciliation 逻辑；
- Scheduled Rule 创建或编辑后如果已经到期，后端会在命令完成前生成对应 Manual occurrence；Dashboard 随后刷新相关 workspace 与统计；
- 删除 Scheduled Rule 只删除未来编排，Dashboard 不把已经生成的 Manual Source 历史级联删除；
- Transaction Workspace 跟随当前消费统计月份列出 Transaction，并展示其 Source description 与当前 Enrichment；
- Income 在 Transaction Workspace 中禁用 Merchant 和消费 Category 控件，只保留 Note 编辑；
- Mapping Review Workspace 只按未匹配 Expense description 聚合 CMB 与 Manual Source 交易，并显示笔数、总金额和来源类型；
- Review 时可以搜索/选择已有 Merchant 或明确新建 Merchant，并选择正式默认 Category；Merchant 相似候选只做提示，不会自动合并；
- Apply 前必须先 Preview；Preview 明确区分 description Mapping、Merchant 默认 Category、保留的 transaction-only 例外和总影响 Enrichment 数量；新 Merchant Apply 还需要二次确认；
- Apply 成功后后端已更新正式 Mapping、affected Enrichment 与两个 Projection，Dashboard 会重新加载 Mapping Review、Transaction Workspace 与统计；
- Transaction Workspace 的 Expense Merchant / Category 修改继续只作为 transaction-only Enrichment exception，不写 Mapping；Note 同样通过 Application/API 修改；
- Expense Category 选项来自 `/api/categories`；“跟随商户默认”发送 `category = null`，由后端执行默认分类语义；
- 保存成功后 Application 已重建两个下游 Projection，Dashboard 再重新加载消费统计和家庭财务摘要；
- 支持各统计与 Application workspace 自己的 loading、空数据、错误和重新加载状态；
- 原消费层严格校验 `schema_version === 2`；家庭财务摘要层严格校验 `schema_version === 1`、安全整数、正负净现金流、月份字段以及 all/shown 两套汇总对账；
- 校验失败时停止对应视图展示，不在前端修正或重新聚合后端事实。

### 多图表 POC

图表 POC 仍从原消费 Dashboard service view model 读取后端已经聚合好的月份/category 数据，不生成第二套统计事实。趋势图按月份正序展示最近 12 个 `show=true` 自然月；月份选择器本身不受 12 个月限制。Income / Cash Flow V1 只增加轻量的家庭现金流摘要和月份数据，不在本轮收敛原六种消费图表 POC，也不新增复杂现金流图表。

当前同时保留六种消费候选展示供真实数据浏览器比较：

- 月度总消费折线图；
- 月度总消费柱状图；
- Category 堆叠柱状图；
- Category 堆叠面积图；
- Category 分组柱状图；
- 当前月份 Category 环形图。

Category 趋势图补齐缺失月份时使用 0，仅作为已有月度 category 聚合的视图转换。图表金额仍保留“分”的整数值，在 tooltip 中统一格式化成人民币。

Chart.js 固定在项目的 npm 依赖中，并由页面加载本地 `node_modules/chart.js/dist/chart.umd.js`；运行 Dashboard 时不使用 CDN，也不发起其他外部网络请求。Chart.js 内置图例交互用于隐藏/恢复 category 系列。

每张图表独立创建和捕获失败。某一图表初始化或渲染失败时，只在该图表卡片显示错误，不影响总览、月份切换、category/merchant 表格或其他图表。

`local_dashboard/api.js` 负责加载消费 Projection、schema 校验、金额/笔数对账和消费 view model；`local_dashboard/financial-summary-api.js` 负责家庭财务摘要 schema v1 加载、签名净现金流校验和 all/shown 对账；`local_dashboard/financial-summary.js` 负责家庭现金流概览 DOM / 月份交互；`local_dashboard/charts.js` 负责纯消费图表配置与图表实例生命周期；`local_dashboard/app.js` 负责消费统计 DOM 状态、月份交互以及将 service 数据交给图表层；`local_dashboard/application-api.js` 统一负责本地 JSON API contract、Manual Input / Scheduled Input / Mapping Review transport 和错误边界；`local_dashboard/manual-entry.js` 负责 Manual Input 创建、列表、更正与删除交互；`local_dashboard/scheduled-input.js` 负责 Scheduled Rule 列表、编辑、启停、删除和 Run Due 交互；`local_dashboard/mapping-review.js` 负责 Expense Mapping Review workspace、Preview 与 Apply 交互；`local_dashboard/transactions.js` 负责 Transaction Workspace 的浏览与单笔例外 / Note 编辑。前端不重新实现 Scheduled due/idempotency、Reconciliation、Mapping propagation、Enrichment 规则、退款归并或财务聚合。

## Rebuild 支持工具

`scripts/` 中保留了本次 Rebuild 期间建立 Merchant Mapping 时使用的截图切行、OCR 和候选匹配检查工具：

```text
scripts/inspect_app_rows.py
scripts/inspect_app_row_ocr.py
scripts/inspect_description_matching.py
scripts/inspect_mapping_candidates.py
```

这些文件属于 Rebuild 过程资产，仍在仓库中维护，因此相关 OCR 依赖继续保留。它们不是正式消费 / 收入统计运行链路，不会被邮件获取、交易重建、退款归并、统计生成或 Dashboard 自动调用。

工具只应读取本地截图、OCR 和交易数据，并把结果写到受 `.gitignore` 保护的本地目录。正式运行时 Expense 只读取两份已审核 Mapping；transaction-only exception 和 Income 默认 Enrichment 来自 persistent Enrichment state。

## 运行测试

完整 Python 测试：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m unittest -q
```

Dashboard JavaScript 测试：

```powershell
node --test local_dashboard/api.test.js local_dashboard/charts.test.js local_dashboard/application-api.test.js local_dashboard/mapping-review-api.test.js local_dashboard/financial-summary-api.test.js
```

Python 编译检查：

```powershell
uv run --frozen python -m compileall -q src tests
```

跨端 frontend / managed runtime 检查：

```powershell
npm run test:dev-runtime
npm run typecheck:frontend
npm run test:frontend
npm run build:web
npm run build:mini:h5
npm run build:mini:weapp
```

需要定位失败时，再对对应模块使用更详细的 test/build 输出，避免正常验证打印无关日志。提交前完整回归同时覆盖 Python、legacy Dashboard JavaScript、managed runtime、frontend typecheck/unit tests 以及 Desktop/H5/WeChat production builds。

## 当前代码结构

```text
package.json
package-lock.json                  # npm install 后生成并应随依赖版本一起提交
.npmrc                             # npm workspace install-strategy=nested

frontend/
├── apps/
│   ├── web/                       # Desktop React/Vite presentation
│   └── mini/                      # Taro Mini + H5 preview presentation
└── packages/
    ├── core/                      # shared contracts/services/view models
    └── design-tokens/

local_dashboard/
├── index.html
├── api.js
├── charts.js
├── app.js
├── styles.css
├── api.test.js
├── charts.test.js
├── financial-summary-api.js
├── financial-summary-api.test.js
├── financial-summary.js
├── application-api.js
├── application-api.test.js
├── manual-entry.js
├── manual-entry.css
├── scheduled-input.js
├── scheduled-input.css
├── mapping-review.js
├── mapping-review.css
├── mapping-review-api.test.js
├── transactions.js
└── transactions.css

scripts/
├── dev-runtime.mjs                # managed single-instance local API/Web/Mini runtime
├── dev-runtime.test.mjs
├── inspect_app_row_ocr.py
├── inspect_app_rows.py
├── inspect_description_matching.py
└── inspect_mapping_candidates.py

src/family_spending/
├── __main__.py                       # `python -m family_spending` entry
├── cli.py                            # serve / sync / jobs / rebuild / diagnose operator CLI
├── backend/
│   ├── paths.py                      # runtime persistent participants
│   ├── state.py                      # CurrentHouseholdSnapshot rehydration
│   ├── pipeline.py                   # Source Sync + Projection rebuild lifecycle
│   ├── runtime.py                    # in-process current snapshot + fingerprint refresh
│   ├── application.py                # canonical Application/API boundary
│   ├── manual_commands.py            # Manual Input create/correct/delete runtime command family
│   ├── scheduled_jobs.py             # batched Scheduled due → one Source Sync
│   ├── projection_queries.py         # read-only generated Projection query helpers
│   └── http_server.py                # canonical local JSON HTTP transport
├── infrastructure/
│   └── file_uow.py                   # shared cross-file rollback / commit boundary
├── feedback.py                       # local product Feedback V1 store/domain
├── source_records.py                 # SourceRecord + SourceAdapter 扩展契约
├── transactions.py                   # Transaction Core + Source Link / 索引
├── reconciliation.py                 # Reconciler 扩展契约 + source-aware 实现
├── enrichment.py                     # Expense / Income type-aware Enrichment current state 与更新规则
├── enrichment_store.py               # Enrichment JSONL storage
├── source_link_store.py              # Source Record → Transaction link storage
├── manual_source.py                  # Manual Source local state + empty-store cleanup
├── scheduled_input.py                # Monthly rule model / storage / occurrence identity
├── mapping.py                        # Expense Mapping loader + type-aware Enrichment resolver
├── mapping_review.py                 # Expense Mapping Review aggregation / preview / propagation
├── month_coverage.py
├── refund_reconciliation.py          # Expense Transaction facts → NetConsumption 派生视图
├── spending_projection.py            # spending + financial Projection build/persist primitives
├── financial_projection.py           # Income + net spending → financial_summary.json schema v1
├── settings.py
├── spending_statistics.py            # AnalyticsProcessor + 消费统计
├── statistics_serialization.py
├── transaction_resolution.py         # shared household domain assembly + review helpers
└── ingestion/
    ├── imap_163.py
    ├── cmb_email_transactions.py
    └── cmb_source_adapter.py

tests/
├── test_backend_application.py
├── test_backend_architecture.py
├── test_backend_http_server.py
├── test_backend_pipeline_integration.py
├── test_cmb_domain.py
├── test_cmb_email_transactions.py
├── test_enrichment_store.py
├── test_feedback.py
├── test_financial_projection.py
├── test_imap_163.py
├── test_income_mapping.py
├── test_local_dashboard.py
├── test_manual_source.py
├── test_mapping.py
├── test_month_coverage.py
├── test_refund_reconciliation.py
├── test_spending_statistics.py
├── test_spending_statistics_merchant_identity.py
├── test_statistics_serialization.py
└── test_transaction_resolution.py
```

## 当前非目标

当前没有实现：

- Income Merchant Mapping；
- 多层或可配置的收入分类体系；Income V1 当前仅使用系统默认 `其他收入`；
- 增长率、同比、环比或复杂变化原因分析；
- “全部月份 / 仅完整月份”切换 UI；
- 最终图表组合收敛；
- AI 消费 / 财务报告；
- 退款分配等更细的诊断明细界面；
- legacy Dashboard 的六种实验性消费图表与更复杂的 chart 组合尚未迁入新 PC Web；当前 Overview 已迁入正式 Spending Statistics 的月份选择、Category 构成/排行与 Top Merchant/display，但还没有迁移这些实验图表或增长率/变化原因分析；
- 后端 Query 已移除主要路径上的逐请求完整 state rebuild，并改为复用 `BackendRuntime` current snapshot；尚未建立正式性能基准，后续性能优化仍按实际阶段耗时与 I/O / Pipeline 证据处理，不依据界面交易条数直接推断瓶颈；
- Mini Automation workspace 的正式迁移，以及微信小程序真机联网与正式发布；当前 Taro WeChat production build 已通过，但仍未配置正式 AppID、HTTPS API 域名和部署环境；
- 面向公网部署的 API、登录、云同步或多用户；
- 数据库、增量统计或常驻 / 系统级后台调度；Scheduled Input V1 只在 Application 初始化、规则 mutation 和显式 Run Due 时执行；
- 其他银行、微信或支付宝独立账单接入。

## 架构说明

领域边界、数据资产、重建关系和隐私原则：

```text
family-consumption-data-architecture-design.md
```

当前 Python 后端的 Runtime / Pipeline / Unit of Work / CLI 技术结构：

```text
backend-technical-architecture-design.md
```
