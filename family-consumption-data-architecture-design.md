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

### 9.3 Enrichment 修改

Enrichment 是重要的可维护状态。

PC Web、微信小程序和未来客户端应通过统一 Application / API 修改：

```text
Merchant
Category
Note
Mapping / Default / Override
```

客户端不直接修改底层存储。

Merchant 改变后，如果 Category 规则要求重新判断 Category，则从 Enrichment 这一阶段处理，并继续刷新依赖 Enrichment 的下游结果。

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

- Manual Input；
- Transaction 查询；
- Reconciliation / Review 用例；
- Merchant / Category / Note 等 Enrichment 修改；
- Mapping / Default / Override 维护；
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
→ downstream processing
→ Analytics / Projection
```

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

## 14. 当前实现迁移约束

当前系统已经有稳定运行的 CMB Email、Merchant Mapping、退款归并和消费统计链路，新架构应渐进迁移，不以重写现有能力为目标。

需要特别保留的事实：

1. 当前 CMB 交易字段必须无损进入 Source Record；
2. 当前 Email 来源是信用卡交易的权威自动 Source；
3. 当前正式 Merchant / Category 规则来自已经人工审核的数据；
4. 当前 `transaction_category_overrides.jsonl` 使用现有 `transaction_id` 定位交易；
5. 因为未来 Transaction ID 与当前 CMB Source Record ID 不再是同一个概念，迁移时必须确保已有 transaction-level override 仍能稳定对应正确 Transaction；
6. 当前消费统计和 Dashboard 属于已验证的下游能力，应作为未来 Analytics / Projection 迁移时的重要兼容基线，而不是被无理由推翻。

具体迁移步骤在 Technical Design / Migration Plan 中确定。

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

在这些边界确定后，下一阶段应进入 Technical Design 与 Migration Plan，基于当前仓库实际实现规划第一条完整纵向迁移切片。
