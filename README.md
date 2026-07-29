# Family Spending Insights

用于获取、整理和分析家庭共同消费数据的本地项目。

当前主流程以招商银行信用卡电子账单为数据来源：

```text
163 邮箱
→ data/emails/*.eml
→ data/transactions.csv
→ Mapping / 分析 / 图表 / 报告
```

当前已完成邮件获取和交易提取。截图 Mapping、消费分析和报告将在后续逐步实现。

## 数据目录

```text
data/
├── emails/
│   └── *.eml
├── screenshots/
├── transactions.csv
└── reports/
```

* `emails/`：从 163 邮箱保存的原始 RFC822 邮件，不可变。
* `screenshots/`：记账 App 截图，供后续 Mapping 使用。
* `transactions.csv`：从全部原始邮件重新生成的统一交易数据。
* `reports/`：后续生成的统计、图表和 AI 报告。

`data/` 不提交到 Git。

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

## 运行测试

运行当前邮件获取和交易提取测试：

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
├── settings.py
└── ingestion/
    ├── imap_163.py
    └── cmb_email_transactions.py
```

* `imap_163.py`：从 163 邮箱保存原始账单邮件。
* `cmb_email_transactions.py`：从原始邮件直接重建统一交易数据。

根目录的早期脚本暂时保留作为历史实现对照，不属于当前主流程。

## 设计说明

整体目标、数据边界和后续方向见：

```text
family-consumption-data-architecture-design.md
```
