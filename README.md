# Family Spending Insights

用于在本地获取、整理和分析家庭共同消费数据。

当前正式数据链路已经包含 CMB Email 与 Manual Source 两个入口，并共享第一版统一领域骨架：

```text
CMB Email / Manual Input
→ Source Adapter / Source Record
→ source-aware Reconciliation
→ Transaction + Source Link
→ current Enrichment
→ 退款归并生成 Net Consumption
→ 月份 / category / merchant Analytics
→ data/reports/spending_statistics.json Projection
→ Application / local JSON API
→ local_dashboard/
```
项目当前已经实现邮件获取、CMB Source Record / Transaction 身份分离、Manual Source 与跨来源 Reconciliation、Manual Input 查询 / 更正 / 删除生命周期、正式 Merchant Mapping、按 description 聚合的 Mapping Review / Mapping Correction、独立持久化的当前 Enrichment、退款归并、消费统计 Projection、本地 JSON Application/API，以及支持 source-native Manual Input 管理、Mapping Review、逐笔 Transaction 浏览和 transaction-only Enrichment exception 的本地 HTML Dashboard。增长率/环比分析、AI 报告、面向公网部署与认证的远程 API、正式微信小程序等仍不在当前实现范围内。
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
├── transaction_source_links.jsonl
├── enrichment_state.jsonl
└── reports/
    └── spending_statistics.json
```

数据职责：
- `emails/`：从 163 邮箱保存的原始 RFC822 邮件，是不可变事实来源；稳定文件名中的账单日期也用于判断自然月数据是否完整。
- `screenshots/`：历史 Merchant Mapping 建立和识别验证使用的本地截图。
- `transactions.csv`：从全部原始邮件全量重建的 CMB 来源级事实数据；进入统一领域模型后对应 `SourceRecord`，不是系统级 Transaction 存储。
- `mappings/merchants.yaml`：人工确认的 `merchant_name → descriptions`。
- `mappings/categories.yaml`：人工确认的 `category → merchant_names`。
- `manual_source_records.jsonl`：Manual Source 的本地来源事实；当前录入保存 `type/date/amount/description/note`，Merchant / Category 由下游 Mapping / Enrichment 决定。更正会以新 Source Record 替换旧记录；删除最后一条 Manual Source 后文件会被移除，缺失文件表示空 Manual Source。
- `transaction_source_links.jsonl`：当前 Source Record → Transaction 关系。
- `enrichment_state.jsonl`：当前 Transaction Enrichment authoritative state；transaction-only Category exception 也持久化在这里，并通过 `category_source` 区分来源。
- `reports/spending_statistics.json`：后端生成、可从正式状态重建的消费统计 Projection。
除两份正式 Mapping 外，`data/` 中的原始邮件、截图、完整交易、运行态 Source/Link/Enrichment 状态、OCR 结果和派生统计默认只保存在本地，不提交到 Git。

正式进入 Git 的数据文件只有：

```text
data/mappings/merchants.yaml
data/mappings/categories.yaml
```

`待分类` 是运行时和界面状态，不是正式 category，也不会写入 Mapping。
## 环境准备

项目要求 Python 3.14 或更高版本，并使用 uv 管理 Python 环境。

```powershell
uv sync
```

本地 Dashboard 图表 POC 使用固定版本的 Chart.js，并通过项目根目录的 npm 依赖安装到本地；运行页面时不依赖 CDN：

```powershell
npm install
```

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

正式 Mapping 与交易事实分开维护，不会把标准商户、分类或复核状态写回 `transactions.csv`。

`load_merchant_mappings()` 读取两份正式配置并建立只读索引：

```text
description → merchant_name
merchant_name → default category
```

历史 `transaction_category_overrides.jsonl` 已完成一次性迁移并从正常 runtime 移除。transaction-only Category exception 现在只存在于 persistent Enrichment state；`transaction_override` 继续作为合法的 `category_source` 表达已迁移的历史决定，但 Mapping loader 不再读取或生成这类单笔事实。

新 Transaction 或缺失 Enrichment state 的正常初始化顺序为：
```text
description 匹配 merchant
→ 获得 merchant 默认 category
→ 生成 merchant_default / unclassified Enrichment
```

主要规则：

- 未匹配 description 时，`merchant_name` 保持空值，`display_name` 使用原始 description，category 为运行态 `待分类`；
- Mapping 只定义稳定的 Merchant 与默认 Category，不承载单笔 transaction exception；
- 已持久化的 `transaction_override` / `manual_override` 不会被普通 Mapping 重建静默覆盖；
- 默认 category 为 `其他支出` 时会产生非阻断复核信号；
- 默认 category 为 `综合购物` 且净消费金额达到高额阈值时会产生非阻断复核信号；
- 复核信号只存在于运行结果中，不写回正式配置。

正常的未分类与 Mapping 错误审核已经收敛为 Mapping Review，而不是默认逐笔编辑 Transaction：

~~~text
unmapped CMB / Manual description
→ 按 description 聚合 Review item
→ 选择已有 Merchant 或明确新建 Merchant
→ 确认 Merchant 默认 Category
→ Preview 影响范围
→ Apply Mapping + affected Enrichment
→ downstream Projection
~~~

Mapping Review 当前规则：
- CMB 与 Manual Source 的未匹配 description 共用同一 Review queue，并按 description 聚合交易笔数、金额和来源类型；
- Merchant 候选只用于提示，用户必须明确选择或新建，不会静默模糊合并；新 Merchant 需要显式二次确认；
- Preview 分别说明 `description → Merchant`、`Merchant → default Category` 的作用范围、被保留的 transaction-only 例外以及本次总影响 Enrichment 数量；
- Apply 只传播到仍然跟随旧 Mapping 的当前 Enrichment state；已有显式 Merchant / Category 单笔例外不会被 Mapping Correction 静默覆盖；
- Preview token 绑定当前 Mapping 选择和受影响状态；预览后状态发生变化时 Apply 会拒绝旧 token，要求重新预览；
- Mapping、affected Enrichment 与 Projection 作为同一个 Application mutation 边界处理；失败时恢复命令前快照，避免留下半提交状态。

运行完整 CMB domain snapshot 的只读诊断：
```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.transaction_resolution
```

该入口会构建与统计主链一致的 CMB domain snapshot，并执行退款净额计算，以便高额 `综合购物` 复核使用净消费金额；它不写 `spending_statistics.json`，也不修改交易或正式 Mapping。fresh CMB-only 诊断只基于当前 Mapping 产生 `merchant_default` / `unclassified`；历史 `transaction_override` 属于 persistent Enrichment，不再由该诊断入口从独立 Mapping 文件恢复。
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

`description` 是用户输入并持久化保存的 Manual Source 原始文本，不是 Canonical Merchant。Manual Input 不直接创建或修改 Merchant / Category；已有 description 如果命中正式 Mapping，会沿用 `description → merchant → default category` 路径，新 description 未命中时进入运行态 `待分类`，后续与 CMB 未匹配 description 共用 Mapping Review。

本地命令行入口：
~~~powershell
$env:PYTHONPATH="src"
uv run python -m family_spending.manual_input `
    --type expense `
    --date 2026-08-08 `
    --amount 88.50 `
    --description "小区门口早餐摊" `
    --note "现金"
~~~

Dashboard 也通过 `POST /api/manual-inputs` 调用同一个 Application use case。录入框会从历史 Manual Source 中读取 distinct description，仅做去空白、大小写与前缀级别的轻量候选提示；用户可以复用已有 description，也可以明确新建，不会自动模糊合并。

Manual Input 会主动执行完整下游 Pipeline：
~~~text
Manual Input
→ Manual Source Adapter
→ Source Record
→ Reconciliation
→ Transaction
→ Enrichment
→ Refund / Net Consumption
→ Spending Statistics
→ schema v2 JSON
~~~

跨来源 Reconciliation 当前规则：
- Manual Source 创建新 Transaction 前，会检查已有 CMB-backed 与 Manual-backed Transaction。
- 找到唯一对应 Transaction 时复用现有 Transaction，不创建重复交易。
- 无匹配时才创建新的 Transaction。
- 多个候选且无法唯一判断时，拒绝该次 Manual Input。
- Category 完全不参与 Transaction identity 判断。
- Merchant 只作为辅助匹配证据。
- CMB 后续到达并匹配 manual-only Transaction 时，复用同一个 Transaction identity，并由 CMB 成为该信用卡交易核心财务事实的 authoritative Source。
- Manual `description` 属于 Source Record 原始事实；Merchant / Category 仍由共享 Mapping / Enrichment 路径决定。Manual `note` 作为用户补充信息进入当前 Enrichment，但不进入 Transaction Core。
- Manual Input 在真正写入前先完成校验与 Reconciliation；写入 Manual Source、Source Link、Enrichment 或 Projection 任一步失败时，会恢复本次命令前的相关文件状态，避免正常故障路径留下半提交。

Manual Source 现在也支持显式查询、更正与删除，并保持 Source 生命周期与 Transaction / Enrichment 边界分离：
- 更正不是通用 Transaction Core PATCH，而是用一个**新的 Manual Source Record ID**替换旧 Source Record，然后重新执行 Reconciliation 与全部必要下游处理。
- 如果更正后的 Source Record 没有匹配到另一个既有 Transaction，系统把它视为同一真实交易的来源事实纠错，保留原系统 Transaction ID；已有 transaction-level Enrichment 也随同该 Transaction identity 保留。
- 在保留 Transaction identity 时，如果当前 Merchant 仍跟随旧 description 的正式 Mapping，会按更正后的 description 重新应用 Mapping；显式 transaction-level Merchant 例外和 Category override 不会被静默覆盖。
- 如果更正后的 Source Record 唯一匹配到另一个既有 Transaction，则按正常 Reconciliation 复用目标 Transaction，不强行保留旧 manual-only Transaction identity，也不会把旧 Transaction 的 Merchant / Category 单笔例外静默复制到目标 Transaction。
- 更正命令显式提供 `note` 时会更新当前 Enrichment Note；Dashboard 编辑器展示并提交的是当前 Transaction Enrichment Note，避免旧 Source note 覆盖后来做过的 Note 修改。
- 删除只删除指定 Manual Source。若 Transaction 还有 CMB 或其他 Source 支撑，则 Transaction 保留；若它只由该 Manual Source 支撑，则重建后该 Transaction 与不再需要的 Enrichment / Source Link 一并退出当前状态。
- 创建、更正、删除都以 Manual Source、Source Link、Enrichment 与 Projection 为同一个 Application mutation 边界；失败时恢复命令前相关文件状态。
本地运行状态包括：

~~~text
data/manual_source_records.jsonl
data/transaction_source_links.jsonl
~~~

这些都属于本地运行数据，不提交到 Git。

当前 Spending Analytics 仍只统计 expense。Manual Source 可以录入 income，但 income 当前只保留为正式 Transaction，不进入现有消费统计。
## Enrichment Application / API

Enrichment 当前是独立持久化的 authoritative current state，保存在：

```text
data/enrichment_state.jsonl
```

Mapping / Merchant default 负责新 Transaction 或缺失状态的初始化；历史 transaction-level override 已迁入同一 persistent Enrichment state。之后的普通统计重建会保留已经存在的 `transaction_override`、`manual_override` 和其他 Enrichment 编辑。Transaction Core 仍不包含 Merchant、Category 或 Note。

本地 Application 提供：
- 创建 source-native Manual Input，并复用现有 Mapping / Manual Source / Cross-source Reconciliation / downstream Pipeline；
- 查询当前 Manual Inputs 及其 Source role、关联 Transaction 与当前 Enrichment；
- 更正 Manual Source：替换 Source identity、重新 Reconciliation，并按实际匹配结果保留原 Transaction identity 或收敛到另一个既有 Transaction；
- 删除 Manual Source，并根据是否仍有其他 Source 支撑决定 Transaction 是否保留；
- 查询历史 Manual description，供录入时做轻量复用提示；
- 查询当前 Transaction + Source identity + Enrichment；
- 查询正式 Category 列表；
- 查询按未匹配 description 聚合的 Mapping Review workspace；
- 预览 `description → Merchant` 与 `Merchant → default Category` 修改的准确影响范围；
- 应用经过预览确认的 Mapping Correction，并只更新仍跟随 Mapping 的当前 Enrichment；
- 修改单笔 Transaction 的 Merchant、Category、Note，作为 transaction-only Enrichment exception；
- Enrichment 修改后只继续执行 Refund / Net Consumption / Analytics / Projection；
- 不因为 Enrichment 编辑重新执行 Source Adapter、Reconciliation 或 Transaction identity 构建。

`category = null` 表示清除显式 Category，并恢复当前 Merchant 的默认分类；如果当前 Merchant 没有默认分类，则回到运行态 `待分类`。
启动最小本地 JSON API：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending.http_api
```

默认监听 `127.0.0.1:8765`，当前端点包括：

```text
GET   /api/health
GET   /api/categories
GET   /api/manual-descriptions
GET   /api/manual-inputs
GET   /api/mapping-reviews
GET   /api/transactions
GET   /api/transactions/{transaction_id}
POST  /api/manual-inputs
POST  /api/manual-inputs/{source_record_id}/corrections
DELETE /api/manual-inputs/{source_record_id}
POST  /api/mapping-reviews/preview
POST  /api/mapping-reviews/apply
PATCH /api/transactions/{transaction_id}/enrichment
```
API 启动时会先执行 `Application.initialize()`，同步当前 Source / Reconciliation / Enrichment 状态并重建最新 Projection。Source 在初始化后发生变化时，旧 Application snapshot 不会静默继续使用失效 links；应重新启动或重新初始化 Application，使上游 Source / Reconciliation 先收敛。

Application mutation 保持 authoritative state 与可重建 Projection 的提交边界。Enrichment PATCH 不得留下 Enrichment 已更新而 Projection 仍旧的半提交状态；Manual Input 的创建、更正和删除都会在跨 Manual Source / Source Link / Enrichment / Projection 写入失败时恢复本次命令前状态；Mapping Review Apply 同样会快照并协调 Mapping、affected Enrichment 与 Projection，任一步失败时恢复本次命令前状态。
## 退款归并

正式统计在 Source Record → Transaction → Enrichment 建立后调用 `reconcile_refunds()`。退款匹配会读取 authoritative Source Record 的 description，并可使用当前 Merchant identity 作为辅助证据；Category 和 transaction override 不参与退款身份判断。

原始金额方向：

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
## 生成消费统计

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.statistics_generation
```

完整后端链路为：
```text
read CMB transactions + Manual Source records
→ read existing Source Links + current Enrichment state
→ load_merchant_mappings()
→ build_household_domain_state()
   → CMB / Manual Adapter + Source Record
   → source-aware Reconciliation / Transaction / Source Link
   → preserve or initialize current Enrichment
→ build_spending_projection()
   → reconcile_refunds() → NetConsumption
   → aggregate_spending()
   → load_month_coverage()
   → serialize schema v2 statistics
→ persist current Source Links / Enrichment state / spending Projection
```
每次显式运行会从当前完整 Source facts、既有 identity links 与 Enrichment current state 重新构建一致的下游结果。当前数据规模不引入退款缓存、数据库或增量统计状态。

统计包含：

- 全部自然月的净消费金额、交易笔数和月份数；
- 当前展示月份的净消费金额、交易笔数和月份数；
- 月份汇总；
- 月份 × category；
- 月份 × merchant/display。

统计规则：

- 未退款消费计 1 笔；
- 部分退款后的消费仍计 1 笔；
- 完全退款、无法匹配的剩余退款和零金额交易不计入；
- 待分类净消费仍进入总额、category 和 merchant/display 对账；
- 待分类 merchant 使用原始 description 作为展示名，但不会成为正式 `merchant_name`；
- 每月总金额和笔数必须分别等于 category 与 merchant/display 汇总之和。
### 自然月完整性与展示策略

信用卡账单约按每月 10 日切分，因此单份账单不能证明一个完整自然月。对于自然月 `M`，后端要求同时存在：

```text
M 月 10 日账单
M+1 月 10 日账单
```

例如 `2026-06` 需要同时存在 `2026-06-10_<hash>.eml` 和 `2026-07-10_<hash>.eml`。

每个月份包含两个独立布尔字段：

- `is_complete`：由原始账单文件覆盖推导出的事实；
- `show`：当前产品展示策略。

当前策略是 `show = is_complete`，但公共契约不把两者合并，未来可以只改变展示策略而保留完整性事实。后端始终保留完整和不完整的全部月份，不在生成阶段删除月份。

派生 JSON 使用 `schema_version = 2`。顶层 `summary` 分为：

- `all_data`：全部月份汇总；
- `shown_data`：仅 `show=true` 月份汇总。
两种汇总均包含金额、交易笔数和月份数，Dashboard 使用 `shown_data`。金额继续使用人民币最小单位“分”的安全整数表示；文件保持确定性字段顺序和原子替换，不包含逐笔来源邮件或退款分配历史。
## 本地消费统计 Dashboard

先安装一次本地图表依赖：

```powershell
npm install
```

Dashboard 现在同时消费消费统计 Projection 与本地 Application/API。需要在两个终端分别启动：

```powershell
# 终端 1：初始化当前领域状态并提供 Transaction / Enrichment API
$env:PYTHONPATH="src"; uv run --frozen python -m family_spending.http_api
```

```powershell
# 终端 2：从项目根目录提供静态文件和 spending_statistics.json
uv run --frozen python -m http.server 8000
```

浏览器访问：

```text
http://localhost:8000/local_dashboard/
```

聚合统计继续直接读取：

```text
/data/reports/spending_statistics.json
```
Manual Input、Mapping Review 与逐笔 Transaction / Enrichment 都通过本地 API `http://127.0.0.1:8765/api` 读取和修改。API 不可用时，已经生成的聚合统计仍可独立展示；Manual Input、Mapping Review 与 Transaction Workspace 会分别显示连接错误。

当前能力：
- 总览使用 `summary.shown_data`，不会把 `show=false` 月份金额混入当前展示；
- 月份选择器只列出 `show=true` 月份，并保留全部符合展示策略的月份；
- 展示后端已经排序的 category 和 merchant/display 汇总；
- 显示待分类项目且不遗漏金额；
- Manual Input 表单支持录入 `type / date / amount / description` 以及可选 `note`；description 先作为 Manual Source 原始事实保存，输入时只从历史 Manual description 提供轻量复用候选；
- Manual Input 管理区列出当前 Manual Source、其 authoritative/supporting role、关联 Transaction identity 与当前 Enrichment，并可选中现有记录执行 Source-level 更正或删除；
- 更正会明确生成新的 Manual Source ID；普通 manual-only 事实纠错在未匹配其他 Transaction 时保留原 Transaction ID，而真正匹配到已有 Transaction 时按 Reconciliation 收敛到该 Transaction；
- 更正编辑器使用当前 Transaction Enrichment Note；Merchant / Category 的稳定 Mapping 修改继续走 Mapping Review，transaction-only Merchant / Category 例外继续走 Transaction Workspace，不混入 Source correction；
- 删除 Manual Source 后，如果没有其他 Source 支撑原 Transaction，则该 Transaction 从当前领域状态移除；若仍有 CMB 等来源支撑，则 Transaction 保留；
- Manual Input 创建、更正或删除成功后由后端完成 Reconciliation 与 Projection 刷新，Dashboard 再重新读取 Manual Input 列表、Mapping Review、Transaction Workspace 与统计；
- Transaction Workspace 跟随当前月份列出 Transaction，并展示其 Source description 与当前 Enrichment；
- Mapping Review Workspace 按未匹配 description 聚合 CMB 与 Manual Source 交易，并显示笔数、总金额和来源类型；
- Review 时可以搜索/选择已有 Merchant 或明确新建 Merchant，并选择正式默认 Category；Merchant 相似候选只做提示，不会自动合并；
- Apply 前必须先 Preview；Preview 明确区分 description Mapping、Merchant 默认 Category、保留的 transaction-only 例外和总影响 Enrichment 数量；新 Merchant Apply 还需要二次确认；
- Apply 成功后后端已更新正式 Mapping、affected Enrichment 与 Projection，Dashboard 会重新加载 Mapping Review、Transaction Workspace 与统计；
- Transaction Workspace 的 Merchant / Category 修改继续只作为 transaction-only Enrichment exception，不写 Mapping；Note 同样通过 Application/API 修改；
- Category 选项来自 `/api/categories`；“跟随商户默认”发送 `category = null`，由后端执行默认分类语义；
- 保存成功后 Application 已重建下游 Projection，Dashboard 再重新加载 `spending_statistics.json`；
- 支持统计与 Transaction Workspace 各自的 loading、空数据、错误和重新加载状态；
- 严格校验 `schema_version === 2`、字段类型、安全整数、月份布尔字段、待分类语义，以及全部月份和展示月份两套汇总对账；
- 校验失败时停止展示，不在前端修正或重新聚合后端事实。
### 多图表 POC

图表 POC 从同一份 Dashboard service view model 读取后端已经聚合好的月份/category 数据，不生成第二套统计事实。趋势图按月份正序展示最近 12 个 `show=true` 自然月；月份选择器本身不受 12 个月限制。

当前同时保留六种候选展示供真实数据浏览器比较：

- 月度总消费折线图；
- 月度总消费柱状图；
- Category 堆叠柱状图；
- Category 堆叠面积图；
- Category 分组柱状图；
- 当前月份 Category 环形图。

Category 趋势图补齐缺失月份时使用 0，仅作为已有月度 category 聚合的视图转换。图表金额仍保留“分”的整数值，在 tooltip 中统一格式化成人民币。

Chart.js 固定在项目的 npm 依赖中，并由页面加载本地 `node_modules/chart.js/dist/chart.umd.js`；运行 Dashboard 时不使用 CDN，也不发起其他外部网络请求。Chart.js 内置图例交互用于隐藏/恢复 category 系列。
每张图表独立创建和捕获失败。某一图表初始化或渲染失败时，只在该图表卡片显示错误，不影响总览、月份切换、category/merchant 表格或其他图表。

`local_dashboard/api.js` 负责加载统计 Projection、schema 校验、金额/笔数对账和 view model；`local_dashboard/charts.js` 负责纯图表配置与图表实例生命周期；`local_dashboard/app.js` 负责统计 DOM 状态、月份交互以及将 service 数据交给图表层；`local_dashboard/application-api.js` 负责本地 JSON API contract、Manual Input management / Mapping Review transport 和错误边界；`local_dashboard/manual-entry.js` 负责 Manual Input 创建、列表、更正与删除交互；`local_dashboard/mapping-review.js` 负责 Mapping Review workspace、Preview 与 Apply 交互；`local_dashboard/transactions.js` 负责 Transaction Workspace 的浏览与单笔例外编辑。前端不重新实现 Reconciliation、Mapping propagation、Enrichment 规则或消费聚合。
## Rebuild 支持工具

`scripts/` 中保留了本次 Rebuild 期间建立 Merchant Mapping 时使用的截图切行、OCR 和候选匹配检查工具：

```text
scripts/inspect_app_rows.py
scripts/inspect_app_row_ocr.py
scripts/inspect_description_matching.py
scripts/inspect_mapping_candidates.py
```

这些文件属于 Rebuild 过程资产，仍在仓库中维护，因此相关 OCR 依赖继续保留。它们不是正式消费统计运行链路，不会被邮件获取、交易重建、退款归并、统计生成或 Dashboard 自动调用。

工具只应读取本地截图、OCR 和交易数据，并把结果写到受 `.gitignore` 保护的本地目录。正式运行时只读取两份已审核 Mapping；transaction-only exception 来自 persistent Enrichment state。
## 运行测试

完整 Python 测试：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m unittest -q
```

Dashboard JavaScript 测试：

```powershell
node --test local_dashboard/api.test.js local_dashboard/charts.test.js local_dashboard/application-api.test.js local_dashboard/mapping-review-api.test.js
```

Python 编译检查：

```powershell
uv run --frozen python -m compileall -q src tests
```

需要定位失败时，再对对应模块使用 `-v`，避免正常验证输出全部测试名称。
## 当前代码结构

```text
package.json
package-lock.json                  # npm install 后生成并应随依赖版本一起提交

local_dashboard/
├── index.html
├── api.js
├── charts.js
├── app.js
├── styles.css
├── api.test.js
├── charts.test.js
├── application-api.js
├── application-api.test.js
├── manual-entry.js
├── manual-entry.css
├── mapping-review.js
├── mapping-review.css
├── mapping-review-api.test.js
├── transactions.js
└── transactions.css

scripts/
├── inspect_app_row_ocr.py
├── inspect_app_rows.py
├── inspect_description_matching.py
└── inspect_mapping_candidates.py
src/family_spending/
├── application.py                    # Manual Source lifecycle + Transaction / Enrichment Application use cases
├── http_api.py                       # 最小本地 JSON transport
├── source_records.py                 # SourceRecord + SourceAdapter 扩展契约
├── transactions.py                   # Transaction Core + Source Link / 索引
├── reconciliation.py                 # Reconciler 扩展契约 + source-aware 实现
├── enrichment.py                     # Enrichment current state 与更新规则
├── enrichment_store.py               # Enrichment JSONL storage
├── source_link_store.py              # Source Record → Transaction link storage
├── manual_source.py                  # Manual Source local state + empty-store cleanup
├── manual_input.py                   # Manual Input create/correct/delete + cross-file rollback boundary
├── mapping.py                        # 正式 Mapping loader + Mapping Enrichment resolver
├── mapping_review.py                 # Mapping Review aggregation / preview / Mapping propagation
├── month_coverage.py
├── refund_reconciliation.py          # Transaction facts → NetConsumption 派生视图
├── spending_projection.py            # downstream-only spending Projection
├── settings.py
├── spending_statistics.py            # AnalyticsProcessor + 消费统计
├── statistics_generation.py          # Source → Transaction → Enrichment → Projection orchestrator
├── statistics_serialization.py
├── transaction_resolution.py         # 共享 CMB domain snapshot + 诊断 CLI
└── ingestion/
    ├── imap_163.py
    ├── cmb_email_transactions.py
    └── cmb_source_adapter.py
tests/
├── test_application.py
├── test_cmb_domain.py
├── test_cmb_email_transactions.py
├── test_enrichment_store.py
├── test_http_api.py
├── test_manual_input_application_api.py
├── test_imap_163.py
├── test_local_dashboard.py
├── test_mapping.py
├── test_mapping_review_application.py
├── test_mapping_review_http_api.py
├── test_month_coverage.py
├── test_refund_reconciliation.py
├── test_spending_statistics.py
├── test_statistics_generation.py
├── test_statistics_serialization.py
└── test_transaction_resolution.py
```
## 当前非目标

当前没有实现：

- 增长率、同比、环比或复杂变化原因分析；
- “全部月份 / 仅完整月份”切换 UI；
- 最终图表组合收敛；
- AI 消费报告；
- 退款分配等更细的诊断明细界面；
- 微信小程序正式客户端；
- 面向公网部署的 API、登录、云同步或多用户；
- 数据库、增量统计或后台调度；
- 其他银行、微信或支付宝独立账单接入。

## 架构说明

系统边界、数据资产、重建关系和隐私原则见：

```text
family-consumption-data-architecture-design.md
```
