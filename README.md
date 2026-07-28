# Family Spending Insights

Family Spending Insights 是一个面向家庭消费分析的个人项目。

项目的目标是自动获取消费数据，形成可靠的交易记录，逐步维护商户和消费语义，计算确定性统计，并最终生成容易阅读的月度消费报告。

## 项目目标

项目主要希望回答以下问题：

* 钱花到了哪里；
* 哪些消费相比过去增加或减少；
* 哪些属于稳定的日常消费；
* 哪些可能属于非日常或冲动消费；
* 当月消费中有哪些值得关注的变化。

整体方向为：

```text
自动获取消费数据
→ 形成可靠的交易数据
→ 维护商户和消费语义
→ 计算确定性统计
→ 生成月度消费报告
```

程序负责数据提取、清洗和统计，AI 负责基于确定性结果生成报告，不负责猜测或修改交易事实。

## 当前范围

当前只优先支持用户正在真实使用的场景：

* 一个 163 邮箱；
* 邮箱目录：`招行信用卡`；
* 邮件关键词：`招商银行信用卡电子账单`；
* 招商银行信用卡电子账单；
* 本地运行和本地数据存储。

当前不计划建设：

* 通用邮件处理框架；
* 多银行插件系统；
* 完整个人财务平台；
* 家庭资产负债管理；
* 数据仓库或微服务；
* 复杂 Web 后端；
* 通用记账应用。

项目结构会随着真实需求逐步成长，不提前建立尚未需要的抽象和分层。

## 当前数据链路

当前仓库中已经实现并通过真实数据验证的链路是：

```text
163 IMAP
→ 保存原始 .eml
→ 解析招商银行账单 HTML
→ statement CSV
→ cleaned transaction CSV
```

对应目录默认为：

```text
data/emails/163/cmb
data/statements/cmb
data/transactions/cmb
```

当前已经确认，未来稳定主链路将简化为：

```text
163 IMAP
→ 原始 .eml
→ transaction CSV
```

`statement CSV` 是重构探索过程中建立的中间产物，当前代码仍然保留，但不再视为未来必须长期维护的数据资产。

原始 `.eml` 是可重新处理的来源数据，交易 CSV 是后续商户语义、消费统计和报告生成的基础数据。

## 数据与隐私

本项目处理真实邮箱和消费账单，所有真实数据默认只保存在本地。

不要提交以下内容：

* `.env`；
* 邮箱地址和授权码；
* 原始 `.eml`；
* 真实账单 CSV；
* 消费截图；
* Mapping 和分析结果；
* 由真实交易生成的小程序数据。

相关目录和文件已经通过 `.gitignore` 排除，包括：

```text
data/
cmb_bill_output/
mapping_output/
tmp/
```

`.env.example` 只保留配置项和非敏感默认值，可以提交到仓库。

## 项目结构

当前主要目录和文件如下：

```text
src/family_spending/
├── ingestion/
│   ├── imap_163.py
│   └── cmb_statement.py
└── cleaning/
    └── cmb_transactions.py

tests/
├── test_imap_163.py
├── test_cmb_statement.py
└── test_cmb_transactions.py

scripts/
miniprogram/

family-consumption-data-architecture-design.md
IMPLEMENTATION_PLAN.md
MINIPROGRAM_LOCAL.md
```

其中：

* `src/family_spending/`：当前重构探索实现；
* `tests/`：对应的自动化测试；
* 根目录旧 Python 脚本：早期账单提取、OCR 和 Mapping 原型；
* `scripts/`：辅助脚本；
* `miniprogram/`：早期微信小程序交互原型；
* `family-consumption-data-architecture-design.md`：项目整体数据架构方向；
* `IMPLEMENTATION_PLAN.md`：旧 OCR Mapping 原型的实施计划；
* `MINIPROGRAM_LOCAL.md`：小程序本地 Mock 使用说明。

当前目录结构不是最终架构，后续会在确认真实需求后逐步简化。

## 本地配置

项目要求：

```text
Python >= 3.14
uv
```

在项目根目录同步环境：

```powershell
uv sync
```

复制配置模板：

```powershell
Copy-Item .env.example .env
```

然后在 `.env` 中填写：

```dotenv
EMAIL_ADDR=
EMAIL_AUTH_CODE=
```

当前默认配置为：

```dotenv
IMAP_HOST=imap.163.com
IMAP_PORT=993

SINCE=01-Sep-2025
MAILBOXES=招行信用卡
KEYWORDS=招商银行信用卡电子账单

CMB_EMAIL_DIR=data/emails/163/cmb
CMB_STATEMENT_DIR=data/statements/cmb
CMB_TRANSACTION_DIR=data/transactions/cmb
```

`EMAIL_AUTH_CODE` 应填写 163 邮箱生成的客户端授权码，而不是邮箱登录密码。

## 运行方式

以下命令均从项目根目录运行。

### 下载原始账单邮件

```powershell
uv run python src/family_spending/ingestion/imap_163.py
```

程序会：

* 登录 163 IMAP；
* 在选择邮箱目录前发送 IMAP `ID`；
* 扫描配置的邮箱目录；
* 筛选指定日期和关键词的邮件；
* 将完整原始邮件保存为 `.eml`；
* 跳过已经保存的邮件。

默认输出：

```text
data/emails/163/cmb
```

### 生成 statement CSV

```powershell
uv run python src/family_spending/ingestion/cmb_statement.py
```

程序会从 `.eml` 中解析招商银行账单 HTML，并生成当前探索链路使用的 statement CSV。

默认输出：

```text
data/statements/cmb
```

这一层是当前过渡实现，后续计划合并进直接生成交易数据的处理流程。

### 生成 cleaned transaction CSV

```powershell
uv run python src/family_spending/cleaning/cmb_transactions.py
```

程序会：

* 使用邮件日期补全交易年份；
* 保留 `Decimal` 金额；
* 保留原始银行描述；
* 生成稳定的 transaction ID；
* 输出清洗后的交易 CSV。

默认输出：

```text
data/transactions/cmb
```

清洗阶段不根据金额正负号推断消费语义，也不使用通用连字符规则拆分支付渠道或商户。

## 测试

在 PowerShell 中运行全部测试：

```powershell
$env:PYTHONPATH="src"; uv run python -m unittest discover -s tests -v
```

当前测试覆盖的主要业务事实包括：

* 163 IMAP `ID` 调用顺序；
* 中文邮箱目录编码；
* 邮件筛选和幂等保存；
* 原始 `.eml` 字节保存；
* MIME、Quoted-Printable 和 charset 解码；
* 招商银行账单交易表格解析；
* `Decimal` 金额；
* 跨年日期补全；
* 稳定 transaction ID；
* 银行原始描述保留。

测试的目标是保护已经通过真实数据确认的业务事实，而不是永久保留当前实现中的所有结构和边缘处理。

## Legacy 原型

仓库根目录仍保留早期原型，包括：

* 直接从 163 邮箱生成招商银行账单 CSV；
* 从记账 App 长截图执行 OCR；
* 将截图交易与银行账单匹配；
* 生成人工维护的 Merchant Mapping；
* 微信小程序本地 Mock。

这些原型验证了部分业务思路和真实数据格式，但不再代表当前项目主线。

相关文件暂时保留，用于参考：

```text
extract_163_cmb_bills_raw_v3.py
build_mapping.py
IMPLEMENTATION_PLAN.md
MINIPROGRAM_LOCAL.md
miniprogram/
```

后续只有经过重新确认仍有价值的业务事实和处理逻辑，才会进入新的稳定实现。

## 当前重构状态

`src/` 和 `tests/` 中的代码应视为：

```text
已经验证业务事实的探索性实现
```

当前实现已经使用真实数据验证：

* 163 IMAP 可以获取目标账单邮件；
* 11 封真实 `.eml` 均可解析；
* 共提取 977 行交易；
* 新旧实现对应字段没有发现差异；
* 一月账单中的十二月交易可以正确归入上一年；
* 当前 transaction ID 方案在真实数据中没有冲突。

这些结果证明核心业务链路可行，但不代表当前模块拆分、中间文件、配置结构和测试数量就是最终设计。

本轮重构的方向是减少不必要的中间层、重复批处理框架和提前抽象，同时保留已经验证的业务事实。

## 下一阶段

下一阶段将按以下顺序推进：

1. 将招商银行处理链路简化为 `.eml → transaction CSV`；
2. 根据当前单一真实场景简化 163 IMAP 获取代码；
3. 重新定义商户、支付渠道和消费语义的维护方式；
4. 建立确定性的月度消费统计；
5. 基于统计结果生成自动化月度消费报告。

README 会随着功能和架构决策同步更新，不提前描述尚未实现的能力。
