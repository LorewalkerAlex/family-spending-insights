# WeChat Mini Program Product & UI Plan

> Status: **authoritative Mini product/UI execution plan**
> Native Mini baseline verified: 2026-08-16
> Backend baseline: Canonical Backend after Atomic Cutover
> Primary development environment: WeChat Developer Tools

本文定义 Family Spending Insights 正式微信小程序的产品范围、页面结构、UI 开发顺序与验收标准。它不建立第二套业务规则；所有财务 truth、Mapping、Review、Manual Input 与 Scheduled Input 行为继续由同一个 Canonical Application / HTTP API 决定。

## 1. Current Baseline

2026-08-16 已完成原生 Mini 技术基线验证：

```text
WeChat Developer Tools
        ↓
Native TypeScript / WXML / WXSS Mini Program
        ↓
wx.request
        ↓
Canonical HTTP API
        ↓
Real household data
```

已验证：

- `frontend/apps/mini` 可直接导入微信开发者工具；
- `project.config.json` 的 `miniprogramRoot` 指向 `miniprogram/`；
- 微信开发者工具可以直接编译 TypeScript 小程序；
- 本地开发时 `wx.request` 可连接 `http://127.0.0.1:8765` Canonical Backend；
- `/api/health` 与真实 Financial Summary 已在开发者工具模拟器中读取成功；
- Taro、React Mini、Mini H5 与 `dist/` 构建入口已经移除；
- 仓库侧 Native Mini tests、TypeScript typecheck、完整 frontend tests/typecheck 与 Desktop build 已通过。

当前首页只用于证明 connectivity 与真实数据链路，**不是正式产品 UI 的设计基线**。后续页面、导航、组件和视觉从原生微信小程序重新设计，不复制旧 Taro/H5 Mini。

## 2. Product Role

Mini 是家庭财务系统的轻量 companion，不是 Desktop 的缩小版。

核心使用场景：

1. 打开后快速看本月花费、收入与结余；
2. 快速确认最近发生的交易；
3. 随手记一笔现金、转账、收入等 Manual Input；
4. 清理少量待分类 / Mapping Review；
5. 查看和处理适合手机完成的轻量自动化与反馈。

Desktop 继续承担：

- Mapping 全量管理；
- Mapping Import / Export；
- Transaction Export；
- Batch operations；
- 复杂诊断与 suggestion evidence/debug；
- 需要大屏空间的高级分析与批量维护。

Mini 不为了功能对齐而复制这些 Desktop surface。

## 3. Top-level Navigation

V1 使用微信原生 `tabBar`，不先实现 custom tab bar。

```text
首页 | 交易 | 记一笔 | 审核 | 更多
```

原因：

- 五个入口都对应高频、明确、独立的手机任务；
- `记一笔` 保持一级入口，避免手工记录藏在二级菜单；
- 原生 tabBar 更稳定，先不为了中心凸起按钮等视觉效果增加自定义导航复杂度；
- 后续如果真实使用证明五 Tab 过重，再基于使用数据收敛。

## 4. Shared UI Principles

### 4.1 Native first

优先使用微信原生 Page / Component / navigation / input / picker 行为。不要为了复用 Desktop 或旧 Mini 代码引入 React/Taro/H5 runtime。

### 4.2 Glanceable finance

手机首页首先回答：

- 这个月花了多少？
- 收入多少？
- 结余多少？
- 最近花在什么地方？
- 有没有需要我处理的交易？

开发环境、Backend 状态、架构说明等信息不进入正式产品首页。

### 4.3 Progressive disclosure

列表只展示完成当前判断所需的关键字段；来源证据、复杂 Mapping 影响、诊断信息放到详情或 Desktop。

### 4.4 One-hand operation

高频 Action 应位于容易点击的位置。输入页减少不必要字段；最小点击目标与表单间距应适合手机触控。

### 4.5 Explicit states

每个真实数据页面必须明确处理：

- loading；
- empty；
- error / retry；
- mutation pending；
- mutation success；
- destructive confirmation（适用时）。

不能依赖空白区域代表状态。

### 4.6 Domain boundary stays server-side

Mini 不复制 Reconciliation、Mapping、Refund、Enrichment 或 Scheduled Input 业务规则。客户端可以做展示转换和轻量表单校验，但 authoritative validation / mutation 必须经过 HTTP Application API。

## 5. Page Plan

### 5.1 首页 Home

目标：10 秒内理解当前家庭财务状态，并看见最重要的下一步。

V1 内容：

- 最新可展示月份；
- **本月支出**作为主数字；
- 收入、净现金流作为次级指标；
- 最近交易 5 条左右；
- 待审核数量 / 入口；
- 明确的 loading / error / empty 状态。

V1 不做：

- 复杂图表；
- 同比/环比；
- 自定义 Dashboard；
- 大量 category 卡片；
- 开发环境信息。

已有 API 优先复用 `financial-summary`、`transactions`、`mapping-reviews`。如果真实数据量导致首页为“最近交易”拉取完整 transaction list 成为明显性能问题，应在 Application/API 增加窄 query 或 pagination，而不是在 Mini 做另一路业务缓存。

### 5.2 交易 Transactions

目标：快速浏览、筛选并打开某一笔交易。

V1：

- 按日期分组的交易列表；
- 月份范围；
- 收入 / 支出基础筛选；
- merchant/display name、category、金额、日期；
- 点击进入 Transaction Detail；
- loading / empty / error。

后续：

- 搜索；
- category / merchant filter；
- 更强的时间范围；
- pagination / incremental loading（在数据量需要时）。

### 5.3 Transaction Detail

V1 以读取为主：

- 金额；
- 日期；
- merchant/display；
- category；
- raw description；
- note；
- source / review signal 的简化信息。

单笔 Enrichment override 是例外路径，不应取代 Mapping Review。是否在 Mini Detail 提供单笔调整，在 Review V1 完成后再评估。

### 5.4 记一笔 Manual Input

目标：在手机上用最少步骤记录非自动来源交易。

V1 字段：

- 类型：支出 / 收入；
- 金额；
- 日期；
- description；
- note（可选）。

行为：

- 使用已有 Manual description 做轻量复用/建议；
- 不在录入时要求用户填写 merchant/category；
- 新 description 若没有 Mapping，进入共享 Mapping Review / 待分类流程；
- 原始 description 保持 source-native，不被客户端改写成 merchant；
- 提交成功后给出明确结果，并允许返回首页或继续记账。

### 5.5 审核 Review

目标：在手机上完成“少量、明确、可确认”的 Mapping Review。

V1：

- 待处理 description/group 列表；
- 展示代表交易与影响范围；
- merchant + category 选择/输入；
- Preview；
- Confirm Apply；
- 新 Merchant 的明确确认；
- Apply 后刷新待审核数量与相关交易。

Mini 不提供完整 Mapping 表格管理、Import/Export 或批量维护。

### 5.6 更多 More

V1/后续承载不适合主 Tab 直接展开的轻量能力：

- Feedback；
- Scheduled Input / Automation；
- About / version；
- 必要的客户端设置。

不放 Backend diagnostics、household file 状态或复杂 operator tools。

## 6. Reusable UI Foundation

正式 UI 开发应从一套小而稳定的原生组件/样式基础开始，不为“设计系统”本身造复杂框架。

Phase 1 预期至少建立：

- global color / spacing / typography tokens；
- Page container / safe-area 规则；
- Section header；
- Money display；
- Summary card；
- Transaction row；
- Empty / Error / Loading state；
- Primary / secondary action styles。

当前 connectivity 页面中的具体颜色、字号、卡片样式都不是正式 token，不需要兼容。

## 7. Delivery Phases

### Phase 0 — Native baseline — DONE

- 删除 Taro/H5 Mini；
- 原生 TypeScript/WXML/WXSS 工程；
- 微信开发者工具可直接导入；
- `wx.request` 连通真实 Canonical Backend；
- 自动测试与 TypeScript 回归通过。

### Phase 1 — UI Foundation + Home V1 — NEXT

一个完整纵向切片完成：

- 建立正式视觉 tokens 与基础组件；
- 建立 5-Tab App Shell；
- 替换 connectivity/demo 首页；
- Home V1 读取真实 Financial Summary；
- Home V1 展示最近交易；
- Home V1 展示待审核入口/数量；
- loading / empty / error；
- unit tests 覆盖数据到 ViewModel 的非平凡转换；
- 微信开发者工具模拟器人工视觉验收；
- 不实现 Transactions / Add / Review 的完整功能，但它们必须有清晰、非误导的导航占位或最小页面，不能出现断路由。

### Phase 2 — Transactions + Detail

- 交易列表；
- 日期分组；
- 基础筛选；
- Transaction Detail；
- Developer Tools + tests。

### Phase 3 — Manual Input

- 原生录入表单；
- description reuse/suggestion；
- Manual Input mutation；
- 成功/失败/重复提交保护；
- Review 衔接。

### Phase 4 — Simple Mapping Review

- Review list/detail；
- Preview；
- Confirm Apply；
- 新 Merchant 确认；
- Apply 后状态刷新。

### Phase 5 — More / Feedback / Scheduled Input

- Feedback；
- Scheduled Input 列表；
- create/update/enable/delete；
- 必要的 secondary settings。

### Phase 6 — Product hardening

- 真机尺寸与 safe area；
- 网络错误与恢复；
- 大数据量表现；
- 触控与可读性；
- 低端设备性能；
- 最终 icon / app copy；
- 体验版回归。

### Phase 7 — Production connectivity & security

在公开域名接入前单独完成：

- HTTPS API origin；
- 微信 request 合法域名；
- 部署配置与 persistent household data root；
- **对公网开放前的身份认证 / 授权机制**；
- 不允许把当前本地无认证 API 直接暴露到公网；
- 备案、域名与小程序平台配置；
- experience/release environment verification。

认证方案属于 Deployment/Security 设计，不在 UI Phase 1 中临时发明。

## 8. Testing and Acceptance

每个 UI slice 默认要求：

1. `npm run test:mini`；
2. `npm run typecheck:mini`；
3. 受影响的 frontend regression；
4. `git diff --check`；
5. 微信开发者工具实际编译；
6. 对本 slice 无法由自动测试证明的 UI/交互做模拟器或真机人工验收。

不再要求 Mini H5 parity。

## 9. Explicit Non-goals for Phase 1

下一 Session 不做：

- 恢复旧 Taro 页面；
- 复刻 Desktop UI；
- 图表大改；
- Mapping management；
- Automation；
- 登录/公网部署；
- custom tab bar；
- 暗色主题；
- 一次性做完全部 Mini 页面。

Phase 1 的成功标准是：**用户打开原生小程序时看到的是一个真实、清晰、可继续扩展的家庭财务首页，而不是技术验证页面。**
