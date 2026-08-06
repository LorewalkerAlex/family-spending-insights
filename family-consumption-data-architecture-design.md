# 家庭消费数据系统架构设计说明

## 1. 文档目的

本文定义 Family Spending Insights 当前已经落地的系统边界、数据资产、派生关系、重建规则、隐私边界和展示端职责。

本文不承担运行手册职责。具体命令、目录入口和测试方式以 `README.md` 为准。

## 2. 当前目标与范围

当前系统以招商银行信用卡电子账单为单一事实来源，目标是形成一套：

- 本地优先；
- 可追溯；
- 可重复全量重建；
- Mapping 独立维护；
- 统计口径由后端统一定义；
- 展示端只读消费派生结果；
- 不泄露完整个人交易数据到公开仓库

的家庭消费数据系统。

当前设计只处理消费事实、退款归并、Merchant Mapping、基础聚合和本地展示，不提前扩展到资产负债、多账户财务运营、远程服务或复杂实时架构。

## 3. 核心设计原则

### 3.1 原始事实与解释分离

原始邮件和从邮件中重建的交易事实不能被 Merchant Mapping、退款归并、统计或展示端反向覆盖。

Mapping、退款匹配、分类、统计和报告都是对原始事实的解释或派生产物。任何下游变化都不能改写原始邮件或伪装成新的银行事实。

### 3.2 独立维护的语义资产

Merchant Mapping 是独立于交易事实长期维护的数据资产：

```text
description → merchant_name
merchant_name → default category
transaction_id → optional category override
```

Mapping 可以变化，但变化只影响重新解析和重新聚合的结果，不修改 `transactions.csv`。

### 3.3 派生结果必须可重建

退款归并结果、语义化交易、统计汇总和展示数据都由上游事实与规则生成。它们可以保存在本地供读取，但必须能够从完整输入重新生成。

当前数据量下，完整重建比增量状态更简单、更可靠，因此不引入退款缓存、数据库或局部更新协议。

### 3.4 统计事实由后端确定

退款、分类、金额方向、交易笔数、月份汇总和对账规则全部由后端实现。

展示端不重新处理退款，不重新执行 Merchant Mapping，不重新排序，不修正后端结果，也不建立第二套统计口径。

### 3.5 未分类不等于遗漏

无法匹配 Merchant 的净消费仍必须进入：

- 月份总消费；
- 月份 × category；
- 月份 × merchant/display；
- 金额与交易笔数对账。

`待分类` 只是运行时和界面标签，不是正式 category。原始 description 可以作为展示名，但不能被写回为已确认 merchant。

### 3.6 隐私优先

完整交易、邮件、截图、OCR 输出和派生统计只保存在本地。公开仓库只跟踪经过人工审核、且不包含完整交易明细的正式 Mapping 配置。

## 4. 当前系统链路

```text
163 邮箱
    ↓
原始 RFC822 邮件
    ↓
交易事实全量重建
    ↓
transactions.csv
    ↓
针对原始完整交易校验 transaction override
    ↓
退款归并
    ↓
退款后的净消费交易
    ↓
Merchant Mapping 与分类解析
    ↓
月份 / category / merchant 聚合
    ↓
版本化统计 JSON
    ↓
本地只读 Dashboard
```

更具体的程序依赖为：

```text
read_transactions_csv()
→ load_merchant_mappings()
→ validate_transaction_overrides()
→ reconcile_refunds()
→ resolve_transactions()
→ aggregate_spending()
→ serialize_spending_statistics()
→ write_spending_statistics_json()
```

该顺序是当前正式契约。展示端或未来报告消费者只能从已经确定的派生结果向下扩展，不能绕过链路重新解释事实。

## 5. 数据资产

### 5.1 不可变外部事实

```text
data/emails/*.eml
```

原始邮件是可追溯来源。程序只负责获取和保存，不把下游语义写回邮件。

### 5.2 可重建交易事实

```text
data/transactions.csv
```

交易 CSV 从全部原始邮件全量重建，保留：

- 稳定 transaction ID；
- 交易日期；
- 银行原始金额方向；
- 银行原始 description；
- 来源邮件；
- 邮件内位置。

它回答“银行账单中发生了哪些记录”，不回答“实际商户是谁”或“属于什么分类”。

### 5.3 独立维护的正式 Mapping

```text
data/mappings/merchants.yaml
data/mappings/categories.yaml
data/mappings/transaction_category_overrides.jsonl
```

三份文件共同构成正式语义资产：

```text
description
→ merchant_name
→ default category
→ optional transaction category override
```

当前没有正式辅助标签体系。一个 description 只能对应一个 merchant；一个 merchant 只能属于一个默认 category；override 只改变单笔最终 category。

### 5.4 可重建统计派生文件

```text
data/reports/spending_statistics.json
```

该文件只包含展示端需要的全局与月份聚合，不复制完整逐笔事实、邮件来源或退款分配历史。

金额使用人民币最小单位“分”的整数表示，并通过 `schema_version` 管理消费端契约。

## 6. 交易事实层

交易事实层负责：

- 获取符合条件的原始邮件；
- 保存完整邮件；
- 解析全部账单；
- 严格校验交易字段；
- 在全部解析成功后原子替换交易 CSV；
- 保持稳定 ID 和可追溯来源。

该层不负责：

- 标准化 merchant；
- 分配 category；
- 匹配退款；
- 生成统计；
- 生成展示数据。

招商银行原始金额方向为：

```text
正数：消费
负数：退款
零：不参与消费统计
```

金额方向的语义转换只在退款归并边界发生。

## 7. Merchant Mapping 层

Mapping 层为交易补充可理解、可统计的语义，但不修改原始交易。

### 7.1 Merchant 解析

```text
原始 description → merchant_name
```

多个 description 可以归入同一个 merchant，但一个 description 不能同时归入多个 merchant。无法可靠确认时保持未匹配状态。

### 7.2 默认分类

```text
merchant_name → default category
```

每个正式 merchant 只有一个默认 category。category 是当前稳定统计维度，不存在正式的多个辅助标签。

### 7.3 单笔覆盖

```text
transaction_id → override category
```

单笔 override 只覆盖该笔交易的最终 category：

- 不替代 description 到 merchant 的匹配；
- 不改变 merchant 默认 category；
- 不改变原始 description；
- 不改变其他交易。

### 7.4 未匹配语义

未匹配交易保持：

```text
merchant_name = null
display_name = 原始 description
category = 待分类
```

`display_name` 仅用于让消费者看见交易主体，不是正式 Mapping。

### 7.5 复核信号

部分默认分类会生成运行时复核信号。复核信号：

- 不阻断解析；
- 不自动改分类；
- 不写入正式 Mapping；
- 不构成交易事实。

## 8. 退款归并层

退款归并发生在净消费 Mapping 与统计之前。

当前顺序为：

1. 优先匹配历史同 description 的同额剩余消费；
2. 若未命中，允许在已确认同 merchant 范围内匹配过去 30 个自然日的同额最近消费；
3. 若仍未命中，按同 description 历史消费从近到远累计扣减；
4. 无法匹配的剩余退款不进入消费统计，只进入运行摘要。

约束：

- 退款只能向前匹配历史消费；
- Merchant 回退只使用 merchant 身份；
- category 和 transaction override 不参与退款判断；
- Merchant 回退不允许不同金额累计；
- 完全退款消费不进入后续净消费集合；
- 部分退款仍保留原消费身份并计为一笔净消费；
- 输出净消费使用下游既有契约的负数金额。

Mapping 变化可能影响同 merchant 回退，因此每次统计生成都重新执行退款归并。

## 9. 统计层

统计层只接收退款处理后的净消费解析结果，并将负数净消费转换为正数展示金额。

当前正式统计维度：

- 全局摘要；
- 月份摘要；
- 月份 × category；
- 月份 × merchant/display。

交易笔数规则：

- 未退款消费计一笔；
- 部分退款后的消费仍计一笔；
- 完全退款消费不计入；
- 无法匹配的剩余退款不计入；
- 零金额交易不计入。

每个月必须满足：

```text
月份总金额
= category 汇总金额之和
= merchant/display 汇总金额之和
```

交易笔数使用同样的对账约束。全局摘要也必须与月份集合一致。

统计文件必须：

- 使用版本化 schema；
- 使用整数分；
- 保持确定性排序与字段顺序；
- 不静默四舍五入超过两位小数的金额；
- 使用原子替换；
- 不包含不必要的逐笔隐私数据。

## 10. 展示与其他消费者

当前正式展示端是本地 HTML Dashboard。

展示端职责：

- 加载版本化统计 JSON；
- 校验 schema 和必要字段；
- 校验金额与交易笔数对账；
- 展示全局和月份汇总；
- 展示 category 和 merchant/display；
- 明确展示待分类状态；
- 提供 loading、空数据、错误和重新加载状态。

展示端不得：

- 读取原始邮件重新解析；
- 重新处理退款；
- 执行 Merchant Mapping；
- 对后端结果重新排序或聚合；
- 自动修复不一致数据；
- 写回交易、Mapping 或统计文件。

未来图表、AI 报告或其他客户端应优先消费同一后端派生契约。确定性数值由程序计算，AI 只解释已经生成的结构化结果，不把主观判断写回事实或 Mapping。

## 11. Rebuild 支持工具边界

仓库保留本次 Rebuild 期间形成的截图切行、OCR 和候选匹配检查脚本。这些工具用于建立和验证 Merchant Mapping 的历史过程，也是后续重新检查证据时可复用的支持能力。

它们与正式主链路的边界是：

- 可以读取本地截图、OCR 输出和交易数据；
- 可以生成本地候选和审核材料；
- 不被正式统计生成自动调用；
- 不直接成为正式 Mapping；
- 只有人工确认后的结果才能进入三份正式 Mapping 文件；
- 相关本地产物继续由 `.gitignore` 保护。

这些文件在 Rebuild 开始后新增，不属于历史遗留清理范围。是否在未来收敛应作为独立决策，而不是因为当前运行时未调用就自动删除。

## 12. 重建规则

### 12.1 邮件或交易解析逻辑变化

```text
重新获取或保留现有原始邮件
→ 重建 transactions.csv
→ 校验 override
→ 重新归并退款
→ 重新解析 Mapping
→ 重建统计 JSON
→ 展示端重新加载
```

### 12.2 Merchant Mapping 变化

```text
原始邮件保持不变
transactions.csv 保持不变
→ 重新校验 override
→ 重新归并退款
→ 重新解析净消费
→ 重建统计 JSON
```

Mapping 变化可能影响同 merchant 退款回退，因此不能只替换展示名称。

### 12.3 退款或统计规则变化

```text
原始邮件和 transactions.csv 保持不变
→ 从对应规则边界重新全量生成下游派生结果
```

### 12.4 Dashboard 纯展示变化

如果后端 schema 和统计事实不变，只需修改展示端。展示端不能借 UI 变化改变后端统计口径。

## 13. Git 与隐私边界

公开仓库可以跟踪：

- 源码；
- 测试；
- 文档；
- 本地 Dashboard 静态文件；
- 三份正式 Mapping 配置。

公开仓库不得跟踪：

- 邮箱凭据；
- 原始邮件；
- 完整交易 CSV；
- App 截图；
- OCR 结果；
- 候选匹配输出；
- 派生统计 JSON；
- 其他包含体系化个人消费明细的本地产物。

删除历史执行代码时，不应自动删除用户磁盘上的历史私有数据；对应 ignore 规则可以继续保留，避免本地产物意外出现在 Git 状态中。

## 14. 当前非目标

当前不建设：

- 多银行或多账单源接入；
- 微信、支付宝独立账单接入；
- 资产负债和家庭预算运营；
- 数据库、微服务或事件驱动架构；
- 实时或增量统计；
- 远程 API、登录、云同步和多用户；
- 正式微信小程序；
- Mapping 编辑器或复核状态持久化；
- 固定图表体系；
- AI Prompt 或长期消费行为画像。

## 15. 设计结论

当前系统采用：

> 本地文件驱动、事实与语义分离、后端统一统计、依赖驱动全量重建的消费数据 Pipeline。

长期稳定资产是：

1. 可追溯的原始邮件和交易事实；
2. 人工确认、独立维护的 Merchant Mapping。

退款结果、语义化净消费、统计 JSON、Dashboard、未来图表和 AI 报告都是依赖这些资产生成的下游消费者。该边界保持当前实现简单、可验证且隐私可控，同时允许未来在不破坏事实层的情况下扩展消费方式。
