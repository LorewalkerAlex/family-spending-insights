# Family Spending Insights

用于获取、整理和分析家庭共同消费数据的本地项目。

当前主流程以招商银行信用卡电子账单为数据来源：

```text
163 邮箱
→ data/emails/*.eml
→ data/transactions.csv
→ Mapping / 分析 / 图表 / 报告
```

当前已完成邮件获取、统一交易提取、App 长截图 OCR 探索、历史 Merchant Mapping 的人工审核和正式配置落地，以及 Mapping loader、单条交易运行时解析和非阻断复核信号。批量解析接入、消费统计和报告仍将在后续逐步实现。

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
```

* `emails/`：从 163 邮箱保存的原始 RFC822 邮件，不可变。
* `screenshots/`：记账 App 截图，只用于历史 Mapping 初始化和识别验证。
* `transactions.csv`：从全部原始邮件重新生成的统一交易事实数据。
* `mappings/merchants.yaml`：人工审核确认的 `merchant_name → descriptions`。
* `mappings/categories.yaml`：人工审核确认的 `category → merchant_names`。
* `mappings/transaction_category_overrides.jsonl`：少量单笔交易的分类覆盖。
* `reports/`：后续生成的统计、图表和 AI 报告。

`data/` 中的原始邮件、完整交易数据、截图、OCR 数据和临时分析结果默认只保存在本地，不提交到 Git。

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
* `amount`：账单金额，保留正负号。
* `description`：招商银行账单中的原始交易描述。
* `source_email`：来源 `.eml` 文件名。
* `source_index`：交易在来源邮件中的顺序，从 1 开始。

`source_email` 和 `source_index` 用于回溯原始邮件，也用于区分业务字段完全相同的真实交易。

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
* 未命中 override、默认 category 为 `综合购物` 且金额 `<= -1000` 时，产生 `high_value_general_shopping_review`；
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

当前实现是纯领域层，没有批量 CSV 输出或独立 CLI。后续统计或界面应在读取交易时动态调用该解析层。

## 运行测试

运行 Mapping loader、正式 Mapping 校验和运行时解析测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest tests.test_mapping -v
```

运行邮件获取和交易提取测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest `
  tests.test_imap_163 `
  tests.test_cmb_email_transactions `
  -v
```

运行仓库全部测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest -v
```

## 当前代码入口

```text
src/family_spending/
├── mapping.py
├── settings.py
└── ingestion/
    ├── imap_163.py
    └── cmb_email_transactions.py
```

* `mapping.py`：读取和校验正式 Mapping，并解析单条交易的 merchant、category 与复核信号。
* `imap_163.py`：从 163 邮箱保存原始账单邮件。
* `cmb_email_transactions.py`：从原始邮件直接重建统一交易数据。

根目录的早期脚本暂时保留作为历史实现对照，不属于当前主流程。

## 设计说明

整体目标、数据边界和后续方向见：

```text
family-consumption-data-architecture-design.md
```
