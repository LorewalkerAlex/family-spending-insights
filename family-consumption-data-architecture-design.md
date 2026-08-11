# 家庭财务数据系统架构设计说明

## 1. 文档目的

本文用于定义 Family Spending Insights 从当前家庭消费分析工具向可扩展家庭财务平台演进时的核心数据模型、数据边界和 High-Level Architecture。

本文关注：

- 外部数据如何进入系统；
- 不同来源的数据如何形成统一的 Transaction；
- Transaction 与 Merchant、Category、Note 等 Enrichment 如何分离；
- 数据从一个阶段修改后如何驱动下游更新；
- 分析、Projection 与客户端如何消费正式数据；
- 存储实现如何与上层数据模型解耦。

本文不讨论具体数据库表、API 协议、类与函数设计、并发控制、部署方式、任务调度技术、缓存策略或具体 UI。

---
## 2. 当前阶段目标

长期目标是形成一个家庭财务平台，可逐步支持：

- 家庭收入与支出管理；
- PC Web 与微信小程序；
- 手动补充非信用卡交易；
- 周期性自动录入；
- Merchant / Category / Note 等信息维护；
- 消费统计、趋势和预测；
- 后续可能出现的其他数据来源与客户端。

当前阶段保持轻量化：

- 核心 Financial Transaction 只建模 `income` 与 `expense`；
- 暂不引入 Transfer、完整 Account、资产负债模型或完整银行账户接入；
- 预测是基于现有正式数据执行的分析行为，不作为新的核心交易模型；
- 招商银行信用卡 Email 是当前最高信任度的自动化交易来源；
- Manual Source 是当前非信用卡交易的主要输入入口；
- Scheduled Input 不是独立 Source，而是自动调用 Manual Source 的流程。

---
## 3. 总体数据 Pipeline

系统从代码执行角度是一条主动调用的下游 Pipeline：

```text
Source Action
    ↓
Adapter
    ↓
Source Record
    ↓
Reconciliation
    ↓
Transaction
    ↓
Enrichment / downstream views
    ↓
Analytics / Projection
    ↓
Application / API
    ↓
Clients
```

核心原则：

> 某一阶段的数据发生变化时，从该阶段开始执行其下游处理；上游数据不受影响。

例如：

```text
Manual Input
→ Manual Adapter
→ Reconciliation
→ Transaction
→ downstream analytics / projection
```

如果修改的是 Enrichment：

```text
Enrichment Update
→ downstream analytics / projection
```
不会因此重新修改 Transaction、Source Record 或已经确定的 Reconciliation 关系。

某个处理阶段可以在执行时读取其他当前状态作为辅助信息。例如 Reconciliation 可以读取当前 Merchant 信息作为匹配证据。这种读取不意味着被读取的数据变成该阶段的上游，也不意味着其变化会自动反向重跑已经完成的上游处理。

---
## 4. Source

### 4.1 定义

Source 表示一种数据进入系统的来源及其自身的数据生命周期。

每个 Source 自己负责：

- 获取或接收原始数据；
- 按自身规则解析或规范化；
- 决定自己的数据如何重新生成或修改；
- 将数据交给 Adapter / Source Record 阶段。

不同 Source 不需要共享完全相同的原始数据生命周期。

### 4.2 当前 Source

当前正式考虑：

```text
CMB Email Source
Manual Source
```

未来可以根据真实需求增加 SMS、其他银行或其他来源。
### 4.3 Scheduled Input

Scheduled Input 不是独立 Source。

它是一个自动化流程：

```text
Schedule / Rule
    ↓
调用 Manual Source
    ↓
Manual Source Record
    ↓
后续 Pipeline
```

因此 Scheduled Input 产生的数据与普通 Manual Input 使用相同的 Source Record、Reconciliation 和 Transaction 创建规则。

---

## 5. Source Artifact

Source Artifact 是可选的原始输入载体。

例如：

```text
一封 CMB .eml
= 一个 Source Artifact
```

一个 Source Artifact 可以产生多条 Source Record。

Manual Source 不需要为了统一模型而强行制造一个独立 Artifact。

Source Artifact 是否存在、如何保存，由具体 Source 决定。

---
## 6. Source Record
### 6.1 定义

Source Record 是某个 Source 经过 Adapter 规范化后，对一笔候选财务事实的来源级表达。

它不是最终 Transaction，而是 Reconciliation 的输入。

第一版公共语义保持最小：

```text
SourceRecord
- id
- source_type
- type
- date
- amount
- currency
- description?      optional
- source-specific provenance
```

其中：

- `id`：Source Record 自己的稳定身份；
- `source_type`：来源类型；
- `type`：`income | expense`；
- `date`：来源记录表达的交易日期；
- `amount`：带符号金额；
- `currency`：币种；
- `description`：来源提供的原始文本，可选；
- source-specific provenance：仅该 Source 需要的追溯信息。
不要求所有 Source 都伪造相同的来源定位字段。
### 6.2 当前 CMB Email 契约映射

当前 `CmbTransaction` / `transactions.csv` 已有字段：

```text
transaction_id
transaction_date
amount
description
source_email
source_index
```

进入新模型时：

```text
SourceRecord.id          ← transaction_id
SourceRecord.source_type ← cmb_email
SourceRecord.type        ← expense
SourceRecord.date        ← transaction_date
SourceRecord.amount      ← amount
SourceRecord.currency    ← CNY
SourceRecord.description ← description

CMB-specific provenance:
- source_email
- source_index
```
因此现有 CMB Email 数据可以无损进入新的 Source Record 模型。

当前 `transaction_id` 在新架构中更接近 CMB Source Record 的来源身份，不应直接假定为未来系统级 Transaction ID。
### 6.3 Manual Source 契约映射

Manual Input 同样先保存来源级原始事实，而不是直接把 Canonical Merchant / Category 当成 Source 字段。当前输入语义为：

```text
type
date
amount
description
note?
```

进入公共 Source Record 时：

```text
SourceRecord.source_type  ← manual
SourceRecord.type         ← type
SourceRecord.date         ← date
SourceRecord.amount       ← amount
SourceRecord.currency     ← CNY
SourceRecord.description  ← 用户原始 description
```
`description` 必须保留用户输入的来源文本，不用规范化 Merchant 覆盖。`note` 是用户补充信息，可在显式 Manual Input command 中进入当前 Enrichment，但不进入 Transaction Core。Merchant / Category 继续通过共享 `description → merchant → default category` Mapping 路径建立。

Manual Input 界面可以读取历史 Manual description 做非常轻量的复用提示，例如忽略空白或大小写差异、有限的前缀候选；这种匹配只用于避免误建重复 description，不是自动 Mapping，也不得静默合并语义不同的文本。用户仍可明确新建 description。新增且未命中 Mapping 的 description 与 CMB 未匹配 description 一样进入统一 Mapping Review。

Manual Source 的来源事实允许由用户显式纠错和删除，但这属于 **Source lifecycle**，不是通用 Transaction Core 编辑：
- correction 用新的 Source Record identity 替换旧 Manual Source Record，再重新进入 Reconciliation；
- deletion 删除指定 Manual Source Record，并让当前 Transaction / Source Link / Enrichment 状态按剩余来源重新收敛；
- Merchant / Category 的稳定语义仍通过 Mapping Review 管理，真正的 transaction-level 例外仍通过 Enrichment command 管理，不把这些职责塞进 Manual Source correction。

---
## 7. Transaction

### 7.1 定义

Transaction 表示当前轻量模型中的真实家庭财务事实。

第一版只包含：

```text
Transaction
- id
- type        income | expense
- date
- amount
- currency
```

如果实现阶段确实需要最小生命周期状态，可以再增加必要字段，但不在当前 HLD 中提前定义。

### 7.2 金额与退款语义

当前统一规则：

```text
type = expense, amount > 0
→ 支出

type = expense, amount < 0
→ 退款 / reversal

type = income, amount > 0
→ 收入
```

退款不是 Income。

信用卡还款不属于当前外部 Expense Transaction。
### 7.3 Transaction 不包含的内容

以下内容不属于 Transaction Core：

```text
raw description
source email
source index
merchant
category
note
OCR confidence
matching evidence
source type
```

这些分别属于 Source、Reconciliation 或 Enrichment。

---

## 8. Reconciliation
### 8.1 定义

Reconciliation 判断一个新的 Source Record 是否对应系统中已经存在的 Transaction。

最终要建立的是：

```text
Source Record
    ↓
Transaction
```

多个 Source Record 可以对应同一个 Transaction。

例如：

```text
Manual Source Record ─┐
                      ├── Transaction
CMB Source Record ─────┘
```

### 8.2 Reconciliation 是 Source-aware 的

不同 Source 的处理规则不是对称的。
#### CMB Email

信用卡数据是对应信用卡交易的权威来源。

规则：

- 合法的 CMB Source Record 最终必须有对应 Transaction；
- 如果没有已有 Transaction，则创建 Transaction；
- 如果匹配到仅由 Manual Source 产生的 Transaction，则关联到同一 Transaction，并以信用卡数据作为该信用卡交易核心事实的权威来源；
- 同一 CMB Source Record 重跑必须保持幂等，不得重复产生 Transaction。

#### Manual Source

Manual Source 在创建新 Transaction 前必须先检查是否已经存在对应交易。

匹配范围包括：

- 已有信用卡 Transaction；
- 历史 Manual Source 已经产生的 Transaction。

只有不存在对应 Transaction 时，Manual Source 才创建新的 Transaction。

Manual Source correction 仍遵循同一 Reconciliation 规则，但要区分 Source identity 与 Transaction identity：
- correction 总是产生新的 Manual Source Record identity；
- 若更正后的来源事实没有匹配到另一个既有 Transaction，则它仍表示同一笔真实交易的来源事实纠错，系统可以保留原 Transaction identity 与该 Transaction 的 current Enrichment；
- 若更正后的来源事实唯一匹配到另一个既有 Transaction，则应收敛到该 Transaction，不为“保留旧 ID”而制造重复 Transaction；旧 Transaction 的 transaction-only Merchant / Category 例外不应静默复制到另一个 Transaction；
- 删除 Manual Source 时，如果同一 Transaction 仍由 CMB 或其他 Source Record 支撑，则 Transaction 保留；如果没有任何剩余 Source 支撑，则该 Transaction 从当前重建状态退出。
### 8.3 匹配证据

第一版匹配主要使用：

```text
type
amount
date
merchant
```

其中：

- `type` 必须语义兼容；
- `amount` 是重要匹配信号；
- `date` 允许合理时间差，而不是必须同一天；
- `merchant` 是辅助身份信号。

Category 完全不参与 Transaction identity matching。

Reconciliation 应尽量保留可解释的匹配证据，而不是只保留一个不可解释的综合 confidence 数值。

### 8.4 Source identity 与 Transaction matching

两者是不同问题：

```text
Source identity
= 判断是不是同一条来源记录

Transaction matching
= 判断两条不同来源记录是不是同一笔真实交易
```

CMB 的 `source_email + source_index` 等 provenance 可以用于稳定来源身份。

跨来源匹配才需要金额、日期、Merchant 等证据。

---
## 9. Enrichment

### 9.1 定义

Enrichment 保存对 Transaction 的解释、补充和用户维护状态。

当前核心包括：

```text
Merchant
Category
Note
```

以及支持这些状态建立或维护的 Mapping / Default / Override 等规则。

### 9.2 与 Transaction 分离

Transaction 与 Enrichment 是不同的数据。

系统不需要长期保存一个已经拼接好的 `EnrichedTransaction` 权威对象。

需要完整视图时：

```text
Transaction
    +
current Enrichment
    ↓
query / analysis-time view
```

Transaction 确定以后，不因为 Merchant、Category 或 Note 的修改而变化。
### 9.3 Mapping Review 与单笔例外

人工审核的主要目标是修正或建立稳定 Mapping path，而不是默认逐笔编辑 Transaction。新账单或 Manual Source 出现未匹配 description 时，正常审核路径应是：

```text
description
→ 确认 / 修正 Merchant Mapping
→ 确认 Merchant 默认 Category
→ 重新应用到受该 Mapping 影响、且没有显式单笔例外的 Transaction
→ downstream Analytics / Projection
```

其中 `description → merchant` 的修改影响对应 description；`merchant → default category` 的修改影响仍跟随该默认值的 Merchant 交易。只有某笔交易实际用途确实偏离稳定 Mapping 时，才使用 transaction-level Enrichment exception。
PC Web、微信小程序和未来客户端必须通过统一 Application / API 执行 Mapping Review、单笔例外以及 Note 修改，不直接写 YAML 或其他底层存储。Application command 负责明确影响范围、更新当前 Enrichment state，并继续刷新下游结果。
### 9.4 Reconciliation 对 Enrichment 的使用

Reconciliation 可以在执行时读取当前 Merchant 等 Enrichment 信息，作为匹配证据。

这只是实时读取当前状态：

- 不会让 Transaction 反向依赖 Enrichment；
- 不会因为以后 Merchant 改变而自动修改已经确认的 Transaction；
- 不会因为 Enrichment 改变而自动重新解释历史 Source Record 与 Transaction 的既有关系。

---
## 10. Analytics 与 Projection

Analytics 消费正式 Transaction 与当前 Enrichment 的组合视图。

可以产生：

- 收入 / 支出统计；
- 月度统计；
- Category / Merchant 汇总；
- 趋势；
- 图表数据；
- Forecast；
- Dashboard projection；
- AI 分析所需结构化输入。

这些结果属于下游派生数据。

它们可以缓存或持久化，但应能够从当前正式数据重新生成。

Forecast 是分析行为，不是新的核心 Transaction 或预测交易事实。

Analytics / Projection 不反向修改 Transaction、Source Record 或 Enrichment。

---
## 11. Application / API 与客户端

PC Web、微信小程序以及未来 App 共用同一套后端 Application / API。

Application / API 承接：

- Manual Input 创建、查询、更正与删除；
- Transaction 查询；
- Reconciliation / Review 用例；
- Merchant / Category / Note 等 Enrichment 修改；
- Mapping / Default / transaction-level exception 维护；
- Analytics / Projection 查询。

客户端负责交互与展示，不复制核心数据处理规则，也不直接操作底层存储。

---
## 12. 数据读写与存储边界

HLD 不绑定具体持久化方案。

上层可以通过统一的数据访问边界理解存储：

```text
Domain / Application
        ↓
Data Read / Data Store
        ↓
Concrete Storage
```

具体实现未来可以根据需要选择文件、SQLite、PostgreSQL 或其他方式。

当前阶段只固定：

- 上层业务模型不依赖具体存储介质；
- 不同领域数据可以使用适合自己的具体存储实现；
- Transaction、Source Record、Enrichment 等正式数据与 Analytics / Projection 派生数据在语义上保持区分。

具体接口、事务能力、表结构和迁移方式属于 Technical Design。

---

## 13. Pipeline 更新原则

整个系统使用单向、主动调用的 Pipeline。

原则：

```text
某阶段发生变化
→ 从该阶段开始调用所有必要的下游处理

上游
→ 不重新执行
→ 不被下游反向修改
```

典型路径：
### Manual Input

```text
Manual Input
→ Manual Adapter
→ Source Record
→ Reconciliation
→ Transaction create / match
→ Mapping / Enrichment
→ downstream processing
→ Analytics / Projection
```

### Manual Source Correction / Delete

```text
Manual Source correction / delete
→ replace or remove Source Record
→ Reconciliation against current remaining sources
→ preserve existing Transaction identity when the corrected source still represents that transaction,
  or converge to another matching Transaction,
  or remove an unsupported Transaction
→ preserve/reapply Enrichment according to Mapping vs transaction-level exception semantics
→ Analytics / Projection
```

这是 Source 阶段的显式 mutation，因此需要重新执行其下游 Reconciliation；它与仅从 Enrichment 阶段向下执行的 Merchant / Category / Note 编辑不同。

### CMB Email

```text
CMB Email
→ CMB Adapter
→ Source Record
→ Reconciliation
→ Transaction create / authoritative match
→ downstream processing
→ Analytics / Projection
```

### Enrichment Update

```text
Enrichment Update
→ downstream processing
→ Analytics / Projection
```
### Analytics Logic Update

```text
Analytics Update
→ Projection refresh
```

不会触碰 Source、Reconciliation、Transaction 或 Enrichment。

---
## 14. 当前实现落地状态与后续迁移约束

当前已经有五条核心领域纵向路径沿本 HLD 的边界落地：CMB Email、Manual Source / Cross-source Reconciliation、Manual Source 生命周期管理、Enrichment 可编辑 / Application API，以及 Mapping Review / Mapping Correction；本地 Dashboard 也已作为第一个真实客户端接入 Application/API，并可直接创建、更正和删除 Manual Input、执行 Mapping Review 和提交 transaction-only Enrichment exception。现有 Email、CSV、正式 Mapping、退款规则与统计 schema v2 契约继续保留。

当前实现状态：
1. `CmbTransaction` / `transactions.csv` 保持原有六字段来源契约，并无损进入 CMB Source Record；CMB Source Record 的既有 `transaction_id` 作为来源身份保留，系统 Transaction 使用独立身份，并通过 Source Record → Transaction 关系连接。
2. CMB Email 继续作为对应信用卡财务事实的 authoritative Source；同一 CMB Source Record 重跑保持幂等。Manual Source 使用独立 Source Record，并在创建 Transaction 前同时检查 CMB-backed 与 Manual-backed Transaction。
3. Cross-source Reconciliation 保持 source-aware / asymmetric：Manual 唯一匹配时复用已有 Transaction，无匹配时创建新 Transaction，多候选无法唯一判断时拒绝；CMB 后到并匹配 manual-only Transaction 时复用同一 Transaction identity，并由 CMB 成为该信用卡财务事实的 authoritative Source。
4. Category 完全不参与 Transaction identity；Merchant 只作为辅助匹配证据。Reconciliation 在实际执行时可以读取当前 Enrichment Merchant，但 Enrichment 修改不会反向重写既有 Source Record → Transaction 关系。
5. Transaction Core 只保留当前 HLD 定义的财务事实；raw description、来源 provenance、Merchant、Category、Note 继续分属 Source / Enrichment，不重新合并进 Transaction。
6. 当前 Enrichment 使用独立持久状态保存 `merchant_name`、`default_category`、`category`、`category_source` 与 `note`。正式 Mapping / Default 用于初始化新 Transaction 或缺失状态；已经持久化的 transaction-level exception 属于当前 Enrichment state，普通统计重建不会覆盖 `transaction_override`、`manual_override` 或其他已有 Enrichment 编辑。
7. 历史 `transaction_category_overrides.jsonl` 已完成一次性迁移：旧 CMB Source Record ID 先通过正式 Source Link 绑定到系统 Transaction，仍有效的历史人工 Category 决定随后写入 persistent Enrichment，并保留 `category_source = transaction_override`。迁移完成后该 JSONL 已从正常 runtime 与正式 Mapping 配置中移除，不再存在第二套单笔 Category 事实来源。
8. Enrichment Merchant 修改在没有显式 Category override 时重新解析 Merchant default；已有显式 `transaction_override` 或 `manual_override` 不会被普通 Merchant 修改静默覆盖。
9. 退款归并不改写或伪造 Transaction 金额，而是生成独立净消费派生结果；Merchant 可以作为退款匹配辅助证据，Category 不参与身份判断。`income` 可以作为正式 Transaction 存在，但当前不进入 spending refund analysis / spending statistics。
10. Application 层已经提供 source-native Manual Input、历史 Manual description 查询、Transaction + 当前 Enrichment 查询，以及 Merchant / Category / Note 修改。Manual Input 只接收 `type/date/amount/description/note`，原始 description 进入 Source Record，再复用现有 Mapping / Cross-source Reconciliation / downstream Pipeline；Enrichment 修改只从 Enrichment 阶段继续重建 refund / analytics / projection，不重新执行 Source Adapter、Reconciliation 或 Transaction identity 形成过程。
11. 本地 JSON HTTP transport 已作为最小真实客户端入口落地，负责把 Manual Input、查询与 Enrichment 修改交给 Application；客户端不直接写 Manual Source、Source Link、`enrichment_state.jsonl`、Mapping 或统计文件。
12. Enrichment state 属于正式当前状态，`spending_statistics.json` 属于可重建 Projection。单次 Enrichment mutation 先生成并写入新的派生 Projection，最后原子替换 authoritative Enrichment state；若 authoritative 写入失败，实现会尝试恢复旧 Projection。Manual Input 在校验和 Reconciliation 完成后才进入持久化，并对 Manual Source、Source Link、Enrichment 与 Projection 建立本次命令级文件快照；任一步写入失败时恢复旧状态，避免正常故障路径留下半提交的业务状态。
13. Source 在 Application 初始化后发生变化时，client-only 查询/编辑不会在旧 link 集合上静默继续；Application 会要求重新执行 `initialize()`，先完成 Source / Reconciliation / downstream 同步。
14. 消费统计继续作为下游 Analytics / Projection。legacy override runtime 依赖移除后，真实正式数据执行完整 rebuild，Source Link、persistent Enrichment 与 `spending_statistics.json` 均与迁移完成前状态保持字节级一致，说明单笔历史决定已由 persistent Enrichment 独立承接。
15. 本地 Dashboard 的聚合视图继续读取 `spending_statistics.json` Projection；Manual Input、逐笔 Transaction 浏览与 Merchant / Category / Note 编辑通过 Application/API 完成。Manual Input 当前输入原始 description，并从后端读取历史 Manual description 做轻量复用提示；新 description 未命中 Mapping 时保持待分类。创建成功后重新读取 Transaction Workspace 与已经由 Application 重建的 Projection。客户端只做展示、筛选和命令提交，不复制 Reconciliation、Enrichment 或消费聚合规则；Application/API 暂时不可用不会改变已生成 Projection 的可读性。
16. 正常审核入口已经落地为 Mapping Review：CMB 与 Manual Source 的未匹配 description 进入同一 workspace，并按 description 聚合交易笔数、金额和来源类型；逐笔 Transaction Workspace 不再承担正常 Mapping 审核职责。
17. Mapping Review Preview 分别计算 `description → Merchant` 和 `Merchant → default Category` 的实际传播范围，并报告被保留的 transaction-only Merchant / Category 例外和总影响 Enrichment 数量。新 Merchant 必须同时确定正式默认 Category，并在 Apply 前显式二次确认。
18. Preview token 绑定当前 Mapping 选择以及与影响范围相关的 Mapping / Enrichment 状态；预览后底层状态变化时旧 token 失效，Apply 必须拒绝并要求重新 Preview，避免影响范围静默扩大或缩小。
19. Mapping Review Apply 会更新 `merchants.yaml` / `categories.yaml`，并只传播到仍跟随修改前 Mapping 的当前 Enrichment state。已有显式 transaction-only Merchant / Category exception 保持原决定，不会被 Mapping Correction 静默覆盖。
20. Mapping Review mutation 从 Mapping / Enrichment 阶段继续执行 downstream Projection，不重新执行 Source Adapter、Reconciliation 或 Transaction identity 构建。Mapping、affected Enrichment 与 Projection 在单次 Application command 中使用文件快照协调；任一步失败时恢复命令前状态。
21. 正常 runtime 现在只加载 `merchants.yaml` 与 `categories.yaml` 两份正式 Mapping；Application initialize、Manual Input、Statistics Generation、Transaction Resolution 和 Mapping Review 均不再接受或读取 legacy override path。`transaction_override` 只作为 persistent Enrichment 的合法 `category_source` 保留，以表达已经迁移且仍需保留的历史单笔决定。
22. Application/API 已增加当前 Manual Input 查询、更正和删除。correction 不是 Transaction Core PATCH，而是新建 replacement Manual Source identity、移除旧 Source identity 并重新执行 source-aware Reconciliation；delete 只作用于指定 Manual Source。
23. Manual-only correction 在更正后未匹配其他 Transaction 时保留原系统 Transaction identity；若 correction 唯一匹配到其他已有 Transaction，则复用该目标 Transaction。前者保留 current transaction-level Enrichment；后者不把旧 Transaction 的 Merchant / Category 单笔例外静默复制到目标 Transaction。
24. 保留 Transaction identity 的 correction 会重新判断当前 Merchant 是否仍跟随旧 description Mapping：仍跟随时按新 description Mapping 更新；显式 transaction-level Merchant exception 与 Category override 保持。显式 correction Note 更新 current Enrichment Note；Dashboard 使用 current Enrichment Note 作为编辑起点。
25. 删除 Manual Source 后，仍有其他 Source 支撑的 Transaction 保留；manual-only Transaction 在失去最后来源后退出当前状态。创建、更正、删除共用跨 Manual Source、Source Link、Enrichment 与 Projection 的命令级 rollback 边界，并已通过真实本地 API create → correction → delete 验证 Source identity 换新、Transaction identity 保留和最终无残留 Manual Source 的生命周期。
当前本地文件持久化只是这一阶段的 concrete storage，不改变 HLD 的存储无关边界。legacy transaction category override → persistent Enrichment 的历史迁移与 runtime 依赖收敛已经完成；后续可以继续按产品价值优先级推进新的完整纵向切片，而无需再维护这条兼容链路。若实现更多 Source、正式客户端或新的存储实现，仍应继续沿相同 Domain / Application 边界扩展，不要为了新能力重新合并 Source、Transaction、Enrichment 或 Analytics。

---
## 15. 当前非目标

当前 HLD 不提前设计：

- Transfer；
- 完整 Account / Asset / Liability；
- 全银行账户接入；
- 微服务；
- 事件总线；
- 数据库表结构；
- API 路由与协议；
- 并发锁与事务机制；
- Scheduler 技术实现；
- 缓存策略；
- 完整审计 / Event Sourcing；
- 固定 Forecast 模型；
- 固定 UI 结构。

这些内容只有在真实需求进入实现阶段并影响当前边界时再设计。

---
## 16. 设计结论

当前系统的基础架构可以概括为：

> **以 Source 为入口、以 Source Record 统一来源数据、通过 Reconciliation 形成 Transaction、将 Enrichment 与 Transaction 分离维护，并通过主动调用的单向 Pipeline 驱动所有下游 Analytics / Projection。**

其中：

- CMB Email 是当前权威自动来源；
- Manual Source 是非信用卡交易的主要人工入口；
- Scheduled Input 只是自动调用 Manual Source；
- Transaction 只表示核心收入 / 支出事实；
- Enrichment 独立保存 Merchant、Category、Note 等状态；
- Reconciliation 在需要时实时读取当前数据进行匹配；
- 某一阶段修改后只继续执行下游，上游保持不变；
- Application / API 为所有客户端提供统一的数据读取和修改入口；
- 具体存储技术通过数据访问边界隔离，留到 Technical Design 决定。
CMB Email、Manual Source / Cross-source Reconciliation、Manual Source 生命周期管理、Enrichment 可编辑 / Application API 与 Mapping Review / Mapping Correction 五条核心纵向切片已经验证这些边界可以在现有代码上落地；本地 Dashboard 进一步验证了客户端可以在不复制核心业务规则的前提下消费 Projection，并通过统一 Application/API 创建 / 更正 / 删除 Manual Input、维护 Mapping 和提交 transaction-only Enrichment exception。后续 Source、Application / API 与客户端能力应继续沿相同领域边界按完整纵向切片推进。
