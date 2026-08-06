# Family Spending Insights

用于获取、整理和分析家庭共同消费数据的本地项目。

当前主流程以招商银行信用卡电子账单为数据来源：

```text
163 邮箱
→ data/emails/*.eml
→ data/transactions.csv
→ 退款归并
→ Merchant Mapping 与 transaction category override
→ 月份 / category / merchant 统计
→ data/reports/spending_statistics.json
```

当前已完成邮件获取、统一交易提取、App 长截图 OCR 探索、历史 Merchant Mapping 的人工审核和正式配置落地、单条及批量交易运行时解析，以及退款归并、消费统计派生文件生成和本地 HTML Dashboard。图表和 AI 报告仍将在后续逐步实现。

## 数据目录

```text
data/
├── emails/
│   └── *.eml
├── screenshots/
├── mappings/
│   ├── merchants.yaml
│   ├── categories.yaml
│   └── transaction_category_overrides.jsonl
├── transactions.csv
└── reports/
    └── spending_statistics.json
```

* `emails/`：从 163 邮箱保存的原始 RFC822 邮件，不可变。
* `screenshots/`：记账 App 截图，只用于历史 Mapping 初始化和识别验证。
* `transactions.csv`：从全部原始邮件重新生成的统一交易事实数据。
* `mappings/merchants.yaml`：人工审核确认的 `merchant_name → descriptions`。
* `mappings/categories.yaml`：人工审核确认的 `category → merchant_names`。
* `mappings/transaction_category_overrides.jsonl`：少量单笔交易的分类覆盖。
* `reports/spending_statistics.json`：由后端全量重建、供本地 Dashboard 读取的消费统计派生文件。

`data/` 中的原始邮件、完整交易数据、截图、OCR 数据、派生统计和临时分析结果默认只保存在本地，不提交到 Git。

以下三份经过人工审核的正式 Mapping 配置会进入 Git：

```text
data/mappings/merchants.yaml
data/mappings/categories.yaml
data/mappings/transaction_category_overrides.jsonl
```

`待分类` 是运行时或界面状态，不是正式 category，也不写入 Mapping 配置。

## 环境准备

安装项目依赖：

```powershell
uv sync
```

复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中填写 163 邮箱账号和授权码：

```dotenv
EMAIL_ADDR=
EMAIL_AUTH_CODE=
```

邮箱目录、账单主题、查询起始日期和数据路径等非隐私配置统一定义在：

```text
src/family_spending/settings.py
```

## 获取原始邮件

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.ingestion.imap_163
```

程序会：

* 登录 163 IMAP；
* 进入招商银行信用卡邮件目录；
* 查找招商银行信用卡电子账单；
* 将完整原始邮件保存到 `data/emails/`；
* 跳过已经存在的邮件，不重复下载完整内容。

原始 `.eml` 是后续所有交易数据的可追溯来源。

## 重建交易数据

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.ingestion.cmb_email_transactions
```

程序会读取 `data/emails/*.eml`，解析所有招商银行账单，并重新生成：

```text
data/transactions.csv
```

处理规则：

* 所有邮件必须成功解析，任意一封失败都会停止；
* 解析全部成功后才会原子替换现有 CSV；
* 信用卡还款记录不会作为消费交易写入；
* 金额保留银行账单中的原始正负方向；
* 银行描述保持原文，不自动拆分支付渠道或商户；
* 外观完全相同的交易不会被自动去重；
* 输出按交易日期和来源位置稳定排序。

## 交易字段

`transactions.csv` 包含以下字段：

* `transaction_id`：根据来源邮件和邮件内位置生成的稳定唯一标识。
* `transaction_date`：交易发生日期。
* `amount`：账单原始金额，保留招商银行的正负方向；正数为消费，负数为退款。
* `description`：招商银行账单中的原始交易描述。
* `source_email`：来源 `.eml` 文件名。
* `source_index`：交易在来源邮件中的顺序，从 1 开始。

`source_email` 和 `source_index` 用于回溯原始邮件，也用于区分业务字段完全相同的真实交易。

`family_spending.ingestion.cmb_email_transactions.read_transactions_csv()` 会按同一 CSV 契约读取交易，并明确拒绝：

* header 与正式字段不一致；
* 缺失、空白或多余字段；
* 无效日期、非有限金额或非正整数 `source_index`；
* 重复 `transaction_id`；
* 无法读取或解码的 CSV。

错误信息会包含文件路径、行号和相关字段。

## Merchant Mapping

正式 Mapping 与 `transactions.csv` 分开维护，不会把标准商户名、分类或复核状态写回交易事实数据。

当前配置包括：

```text
data/mappings/merchants.yaml
data/mappings/categories.yaml
data/mappings/transaction_category_overrides.jsonl
```

`family_spending.mapping.load_merchant_mappings()` 会读取并校验三份配置，建立以下只读索引：

```text
description → merchant_name
merchant_name → default category
transaction_id → override category
```

校验会明确拒绝重复 description、跨 category 重复 merchant、两份 YAML 的 merchant 集合不一致、空名称或空列表、重复 override、未知 override category，以及无法解析的 YAML 或 JSONL。错误信息会包含对应文件和值；YAML 重复 key 不会被静默覆盖。

单条交易通过 `family_spending.mapping.resolve_transaction()` 解析：

```text
description 匹配 merchant
→ 得到 merchant 默认 category
→ transaction_id 命中 override 时只覆盖最终 category
```

运行时规则：

* description 已映射时，`display_name` 使用标准 `merchant_name`；
* description 未映射时，保留原始 description，最终 category 为运行态 `待分类`；
* override 只能用于已经匹配 merchant 的交易；如果 ID 命中 override 但 description 无法匹配，会作为数据一致性错误明确失败；
* override 只改变该笔交易的最终 category，不改变 merchant、merchant 默认 category 或原始交易；
* 命中 override 的交易不产生复核信号；
* 未命中 override 且默认 category 为 `其他支出` 时，产生 `other_expense_review`；
* 未命中 override、默认 category 为 `综合购物` 且规范化净消费金额 `<= -1000` 时，产生 `high_value_general_shopping_review`；
* 复核信号只存在于本次解析结果中，不持久化、不阻断处理，也不修改正式 Mapping。

`ResolvedTransaction` 保留原始 `CmbTransaction`，并提供：

```text
merchant_name
display_name
default_category
category
category_source
is_unmatched
review_signals
```

## 批量交易解析

运行只读检查：

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.transaction_resolution
```

程序会：

1. 严格读取完整的 `data/transactions.csv`；
2. 一次加载三份正式 Mapping；
3. 针对原始完整交易验证所有正式 transaction override；
4. 对每条原始交易调用现有单条 resolver；
5. 输出解析来源汇总、待分类交易和复核项。

该入口继续用于检查原始完整交易与 Mapping 的一致性。它不会处理退款，也不会生成统计文件。

汇总包括：

```text
Transactions
Merchant defaults
Transaction overrides
Unclassified
other_expense_review
high_value_general_shopping_review
```

待分类和复核项会显示 `transaction_id`、日期、金额、原始 description、标准显示名和最终 category。正常交易不会逐笔打印。

该入口完全只读：

* 不重写 `transactions.csv`；
* 不修改正式 Mapping；
* 不记录提醒处理状态；
* 不生成报告文件；
* 不自动修复任何数据。

交易 CSV、Mapping 或 override 与完整交易数据不一致时，命令会明确失败，不输出看似成功的部分汇总。

应用层公开入口：

```text
read_transactions_csv()
resolve_transactions()
validate_transaction_overrides()
resolve_transactions_from_files()
format_transaction_resolution_report()
```

## 退款归并

正式消费统计会在 Merchant Mapping 之前调用：

```text
family_spending.refund_reconciliation.reconcile_refunds()
```

退款规则：

* 招商银行原始金额中 `amount > 0` 为消费，`amount < 0` 为退款；零金额不进入退款、Mapping 或统计，并在运行摘要中单独计数；
* 每个 description 内按交易日期处理，同一天使用原始输入位置作为稳定顺序；
* 退款只向前查找，不能抵消未来消费；
* 第一优先级是在原始精确 `description` 相同的历史交易中，从近到远寻找“当前剩余消费金额与退款金额完全相同”的单笔消费；
* 若精确 description 没有同额候选，且消费与退款 description 均已映射到同一个 Merchant，则允许匹配过去 30 个自然日内、当前剩余金额完全相同的最近一笔消费；同日仍以原始输入顺序为准；
* Merchant 回退只使用已确认的 `description → merchant_name` 身份，不使用 category 或 transaction override，也不允许不同金额累计抵消；
* Merchant 回退仍找不到时，才从最近的同 description 历史消费开始逐笔累计扣减；
* 部分退款保留原消费的 transaction ID、日期、description 和来源，并将剩余消费规范化为负数金额；
* 完全退款消费不会进入后续 Mapping、统计或交易笔数；
* 无法匹配完的剩余退款不会进入 Mapping 或统计，只保留数量和金额汇总；
* 输出净消费保持原消费在输入 tuple 中的顺序，并统一使用下游既有契约所需的负数净消费金额。

退款归并层以原始交易为事实输入，并仅借用已确认的 description→merchant_name 作为保守回退匹配身份；它不依赖 category、transaction override 或 JSON schema。Merchant Mapping 变化可能改变回退匹配结果，因此统计命令每次都从完整原始交易重新归并。招商银行原始正负方向也只在这一层转换，后续 Mapping 与统计继续使用负数净消费契约。

## 生成消费统计

运行完整后端重建：

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.statistics_generation
```

完整链路为：

```text
read_transactions_csv()
→ load_merchant_mappings()
→ validate_transaction_overrides() 针对原始完整交易
→ reconcile_refunds() 使用 description 和同 Merchant 30 天回退
→ resolve_transactions() 针对退款后的净消费
→ aggregate_spending()
→ serialize_spending_statistics()
→ 原子替换 spending_statistics.json
```

每次运行都会从 `transactions.csv` 全量重建。Merchant、Category 或 override 变化时不需要重新解析邮件，只需再次运行统计生成命令。当前家庭消费数据规模不建立退款缓存、SQLite 或局部增量状态。

命令只输出聚合摘要：

```text
Raw transactions
Zero-amount transactions ignored
Refund transactions
Same-merchant refund matches
Same-merchant matched amount
Net consumption transactions
Fully refunded transactions
Partially refunded transactions
Unmatched refunds
Unmatched refund amount
Unclassified net transactions
Months
Total net spending
Output
```

不会打印完整真实消费明细。

## 统计口径

统计输入只包含退款处理后的负数净消费，聚合时转为正数消费金额。

第一版统计包括：

* 全部月份总消费和净消费交易笔数；
* 月份汇总；
* 月份 × category；
* 月份 × merchant/display。

交易笔数规则：

* 未退款消费计 1 笔；
* 部分退款后的消费仍计 1 笔；
* 完全退款消费不计入；
* 无法匹配的剩余退款不计入；
* 零金额交易不计入。

每个月保证：

```text
月份总消费
= category 金额之和
= merchant/display 金额之和
```

对应的 transaction count 也保持一致。

待分类净消费仍计入月份总消费：

* category 使用运行态标签 `待分类`；
* `merchant_name` 保持 `null`；
* `display_name` 使用原始 description；
* `is_unclassified` 为 `true`；
* 原始 description 不会写回正式 Mapping。

月份按最近月份优先排序。月份内部的 category 和 merchant/display 按消费金额降序排列，名称作为稳定 tie-breaker。

## 派生统计 JSON

输出路径：

```text
data/reports/spending_statistics.json
```

顶层结构：

```json
{
  "schema_version": 1,
  "summary": {
    "total_spending_minor": 123456,
    "transaction_count": 42,
    "month_count": 3
  },
  "months": []
}
```

金额使用人民币最小单位“分”的整数表示：

```text
1234.56 元 → 123456
```

如果金额包含超过两位小数，生成过程会明确失败，不会静默四舍五入。

派生文件：

* 使用 UTF-8 和确定性字段顺序；
* 不包含生成时间，重复输入会产生相同内容；
* 不复制逐笔 transaction ID、来源邮件或退款分配历史；
* 只包含前端需要的汇总、标准 Merchant 名和待分类 display name；
* 先写同目录临时文件，完成 `flush` 和 `fsync` 后使用 `os.replace()` 原子替换；
* 写入或替换失败时不会破坏已有正式文件；
* 由 `.gitignore` 保持在本地，不进入公开仓库。

后续如果前端读取方式或 schema 发生变化，只需要调整统计领域层或序列化层，不需要修改退款与 Mapping 契约。

## 本地消费统计 Dashboard

`local_dashboard/` 是从当前正式统计契约出发独立实现的本地消费端。旧的 `miniprogram/`、Mock、导出脚本和其他可行性测试代码不构成目录、API、数据模型、UI 或兼容标准。

先从项目根目录生成最新统计：

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.statistics_generation
```

再从项目根目录启动 Python 标准库静态服务：

```powershell
uv run python -m http.server 8000
```

浏览器访问：

```text
http://localhost:8000/local_dashboard/
```

页面直接读取：

```text
/data/reports/spending_statistics.json
```

当前能力：

* 展示累计净消费、净消费交易笔数和统计月份数；
* 默认选择后端返回的第一个月份，并支持切换其他月份；
* 按后端既有顺序展示月份 category 和 merchant/display 统计；
* 待分类商户使用原始 `display_name`，同时显示 `待分类` 标记，金额不会被遗漏；
* 支持 loading、全局空数据、月份列表空数据和明确错误状态；
* 明确校验 `schema_version === 1`、必要字段、安全整数和待分类 merchant 语义；
* 校验每个月及全局的金额、交易笔数和月份数量对账；
* 校验失败时停止展示，不在前端重新聚合或修正后端结果；
* “重新加载”会绕过浏览器缓存重新读取正式派生文件。

Dashboard 不重新处理退款、不执行 Merchant Mapping、不重新排序、不写回任何数据，也不请求外部网络资源。真实 `spending_statistics.json` 继续由 `.gitignore` 保持在本地；测试只使用虚构数据。

`local_dashboard/api.js` 公开 `getSummary()`、`getMonths()`、`getMonthStatistics(month)` 和 `reloadStatistics()`，并将数据加载、schema 校验、对账校验和 view model 转换与 DOM 渲染分离。以后迁移到微信小程序时可以参考这些职责和返回模型，但浏览器 `fetch`、DOM 和 CSS 代码不需要直接复制。

当前 Dashboard 不包含图表、逐笔交易、退款明细、Mapping 编辑、复核处理、登录、云同步、远程 API 或前端写回。

## 运行测试

运行 Mapping loader、正式 Mapping 校验和运行时解析测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest tests.test_mapping -q
```

运行邮件获取、交易提取和 CSV reader 测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest `
  tests.test_imap_163 `
  tests.test_cmb_email_transactions `
  -q
```

运行批量交易解析应用层测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest tests.test_transaction_resolution -q
```

运行退款、聚合、序列化和完整统计生成测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest `
  tests.test_refund_reconciliation `
  tests.test_spending_statistics `
  tests.test_statistics_serialization `
  tests.test_statistics_generation `
  -q
```

运行本地 Dashboard 的 JavaScript service 测试：

```powershell
node --test local_dashboard/api.test.js
```

运行 Dashboard 静态契约测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest tests.test_local_dashboard -q
```

运行仓库全部 Python 测试（成功时只输出简短汇总）：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest -q
```

如果测试失败，再对失败模块使用 `-v` 查看逐项详情，避免正常验证时输出全部测试名称。

## 当前代码入口

```text
local_dashboard/
├── index.html
├── api.js
├── app.js
├── styles.css
└── api.test.js

src/family_spending/
├── mapping.py
├── refund_reconciliation.py
├── settings.py
├── spending_statistics.py
├── statistics_generation.py
├── statistics_serialization.py
├── transaction_resolution.py
└── ingestion/
    ├── imap_163.py
    └── cmb_email_transactions.py
```

* `local_dashboard/api.js`：读取并严格校验正式派生统计，提供独立于 DOM 的 service 和 view model。
* `local_dashboard/app.js`：管理页面加载、月份切换、分类/商户视图以及空态和错误态。
* `local_dashboard/styles.css`：提供无外部依赖、移动优先的本地 Dashboard 样式。
* `mapping.py`：读取和校验正式 Mapping，并解析单条交易的 merchant、category 与复核信号。
* `refund_reconciliation.py`：优先按原始 description，并以同 Merchant 30 天同额作为保守回退，将退款向前归并到历史消费。
* `spending_statistics.py`：将净消费解析结果聚合为月份、category 和 merchant/display 领域对象。
* `statistics_serialization.py`：将统计领域对象转换为版本化 JSON，并负责原子写入。
* `statistics_generation.py`：编排完整重建流程并提供正式 CLI。
* `transaction_resolution.py`：批量解析完整交易数据、针对原始交易校验 override，并输出只读检查结果。
* `imap_163.py`：从 163 邮箱保存原始账单邮件。
* `cmb_email_transactions.py`：从原始邮件重建统一交易数据，并按正式 CSV 契约读取交易。

`miniprogram/`、根目录早期脚本、Mock 和导出工具仅作为历史 POC 保留，不属于当前主流程，也不构成新功能的设计标准或兼容约束。

## 设计说明

整体目标、数据边界和后续方向见：

```text
family-consumption-data-architecture-design.md
```
