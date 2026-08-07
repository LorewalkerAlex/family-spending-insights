# Family Spending Insights

用于在本地获取、整理和分析家庭共同消费数据。

当前正式主链路以招商银行信用卡电子账单为事实来源：

```text
163 邮箱
→ data/emails/*.eml
→ data/transactions.csv
→ 校验 transaction category override
→ 退款归并
→ Merchant Mapping
→ 月份 / category / merchant 统计
→ 根据账单文件名判断自然月完整性与展示策略
→ data/reports/spending_statistics.json
→ local_dashboard/
```

项目当前已经实现邮件获取、交易事实重建、正式 Merchant Mapping、退款归并、消费统计派生文件、本地 HTML Dashboard，以及用于比较多种展示形式的本地图表 POC。增长率/环比分析、AI 报告、远程 API 和正式微信小程序仍不在当前实现范围内。

## 数据与隐私边界

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

数据职责：

- `emails/`：从 163 邮箱保存的原始 RFC822 邮件，是不可变事实来源；稳定文件名中的账单日期也用于判断自然月数据是否完整。
- `screenshots/`：历史 Merchant Mapping 建立和识别验证使用的本地截图。
- `transactions.csv`：从全部原始邮件全量重建的交易事实数据。
- `mappings/merchants.yaml`：人工确认的 `merchant_name → descriptions`。
- `mappings/categories.yaml`：人工确认的 `category → merchant_names`。
- `mappings/transaction_category_overrides.jsonl`：少量单笔交易的分类覆盖。
- `reports/spending_statistics.json`：后端生成、供 Dashboard 只读消费的统计派生文件。

除三份正式 Mapping 外，`data/` 中的原始邮件、截图、完整交易、OCR 结果和派生统计默认只保存在本地，不提交到 Git。

正式进入 Git 的数据文件只有：

```text
data/mappings/merchants.yaml
data/mappings/categories.yaml
data/mappings/transaction_category_overrides.jsonl
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
- `transaction_id` 由来源邮件和邮件内位置稳定生成。

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

## Merchant Mapping

正式 Mapping 与交易事实分开维护，不会把标准商户、分类或复核状态写回 `transactions.csv`。

`load_merchant_mappings()` 读取三份正式配置并建立只读索引：

```text
description → merchant_name
merchant_name → default category
transaction_id → override category
```

运行顺序为：

```text
description 匹配 merchant
→ 获得 merchant 默认 category
→ transaction_id 命中 override 时只覆盖该笔最终 category
```

主要规则：

- 未匹配 description 时，`merchant_name` 保持空值，`display_name` 使用原始 description，category 为运行态 `待分类`；
- override 只能覆盖已匹配 merchant 的交易，不能代替 Merchant Mapping；
- override 不修改 merchant 默认分类，也不修改原始交易；
- 默认 category 为 `其他支出` 时会产生非阻断复核信号；
- 默认 category 为 `综合购物` 且净消费金额达到高额阈值时会产生非阻断复核信号；
- 复核信号只存在于运行结果中，不写回正式配置。

运行原始完整交易与 Mapping 的只读一致性检查：

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.transaction_resolution
```

该入口不处理退款、不生成报告、不修改交易或 Mapping。

## 退款归并

正式统计在应用交易分类前调用 `reconcile_refunds()`。

原始金额方向：

```text
amount > 0：消费
amount < 0：退款
amount = 0：忽略并单独计数
```

匹配顺序：

1. 在历史同 description 消费中匹配当前剩余金额完全相同的最近一笔；
2. 若未命中，并且双方 description 已映射到同一 merchant，则在过去 30 个自然日内匹配同额的最近一笔消费；
3. 若仍未命中，再按同 description 历史消费从近到远累计扣减；
4. 无法匹配的剩余退款不进入统计，只记录数量和金额摘要。

退款只能抵消历史消费，不能抵消未来交易。Merchant 回退只使用 `description → merchant_name` 身份，不使用 category 或 transaction override。

部分退款保留原消费的 ID、日期、description 和来源；完全退款的消费不进入后续 Mapping、统计或交易笔数。退款归并后的净消费统一转换为下游既有契约使用的负数金额。

## 生成消费统计

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.statistics_generation
```

完整后端链路为：

```text
read_transactions_csv()
→ load_merchant_mappings()
→ validate_transaction_overrides() 针对原始完整交易
→ reconcile_refunds()
→ resolve_transactions() 针对退款后的净消费
→ aggregate_spending()
→ load_month_coverage() 读取账单文件名覆盖
→ serialize_spending_statistics()
→ 原子替换 spending_statistics.json
```

每次运行都从完整事实数据全量重建。当前数据规模不引入退款缓存、数据库或增量状态。

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

再生成最新统计：

```powershell
$env:PYTHONPATH="src"; uv run python -m family_spending.statistics_generation
```

从项目根目录启动静态服务：

```powershell
uv run python -m http.server 8000
```

浏览器访问：

```text
http://localhost:8000/local_dashboard/
```

Dashboard 直接读取：

```text
/data/reports/spending_statistics.json
```

当前能力：

- 总览使用 `summary.shown_data`，不会把 `show=false` 月份金额混入当前展示；
- 月份选择器只列出 `show=true` 月份，并保留全部符合展示策略的月份；
- 展示后端已经排序的 category 和 merchant/display 汇总；
- 显示待分类项目且不遗漏金额；
- 支持 loading、空数据、错误和重新加载状态；
- 严格校验 `schema_version === 2`、字段类型、安全整数、月份布尔字段、待分类语义，以及全部月份和展示月份两套汇总对账；
- 校验失败时停止展示，不在前端修正或重新聚合后端事实；
- 不写回任何数据。

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

`local_dashboard/api.js` 负责加载、schema 校验、金额/笔数对账和 view model；`local_dashboard/charts.js` 负责纯图表配置与图表实例生命周期；`local_dashboard/app.js` 负责 DOM 状态、月份交互以及将 service 数据交给图表层。未来其他客户端可以参考 service 职责和返回模型，但不需要复用浏览器 DOM、Chart.js 或 CSS 实现。

## Rebuild 支持工具

`scripts/` 中保留了本次 Rebuild 期间建立 Merchant Mapping 时使用的截图切行、OCR 和候选匹配检查工具：

```text
scripts/inspect_app_rows.py
scripts/inspect_app_row_ocr.py
scripts/inspect_description_matching.py
scripts/inspect_mapping_candidates.py
```

这些文件属于 Rebuild 过程资产，仍在仓库中维护，因此相关 OCR 依赖继续保留。它们不是正式消费统计运行链路，不会被邮件获取、交易重建、退款归并、统计生成或 Dashboard 自动调用。

工具只应读取本地截图、OCR 和交易数据，并把结果写到受 `.gitignore` 保护的本地目录。正式运行时仍只读取三份已审核 Mapping。

## 运行测试

完整 Python 测试：

```powershell
$env:PYTHONPATH="src"; uv run --frozen python -m unittest -q
```

Dashboard JavaScript 测试：

```powershell
node --test local_dashboard/api.test.js local_dashboard/charts.test.js
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
└── charts.test.js

scripts/
├── inspect_app_row_ocr.py
├── inspect_app_rows.py
├── inspect_description_matching.py
└── inspect_mapping_candidates.py

src/family_spending/
├── mapping.py
├── month_coverage.py
├── refund_reconciliation.py
├── settings.py
├── spending_statistics.py
├── statistics_generation.py
├── statistics_serialization.py
├── transaction_resolution.py
└── ingestion/
    ├── imap_163.py
    └── cmb_email_transactions.py

tests/
├── test_cmb_email_transactions.py
├── test_imap_163.py
├── test_local_dashboard.py
├── test_mapping.py
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
- 逐笔交易或退款明细界面；
- Mapping 编辑器和复核处理界面；
- 微信小程序正式客户端；
- 远程 API、登录、云同步或多用户；
- 数据库、增量统计或后台调度；
- 其他银行、微信或支付宝独立账单接入。

## 架构说明

系统边界、数据资产、重建关系和隐私原则见：

```text
family-consumption-data-architecture-design.md
```
