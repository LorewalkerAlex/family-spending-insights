# 微信小程序本地 Mock 版

当前版本不需要 Docker、SQL、后端 API 或微信 `openid`。小程序直接读取由现有账单结果生成的本地 Mock 数据，交互修改保存在微信开发者工具的本地缓存中。

## 已实现

- 按日期分组的流水列表。
- 月份选择、商户/分类搜索。
- 全部、待整理、退款筛选。
- 月度净支出、支出、退款、交易笔数和待整理数量。
- 流水详情与标准商户、分类的本地修改。
- Mapping 建议确认、修改后确认、忽略。
- 本地操作重置。
- 统一 `services/api.js` 数据服务，后续只需替换该文件即可连接 FastAPI。

## 在微信开发者工具中运行

1. 打开微信开发者工具。
2. 选择“导入项目”。
3. 项目目录选择本仓库的 `miniprogram` 文件夹。
4. 开发阶段可使用测试号或游客模式。
5. 编译后默认进入流水页面。

项目配置中关闭了请求域名校验，但当前版本不会发起网络请求。

## 更新 Mock 数据

后台账单结果更新后，在项目根目录运行：

```powershell
uv run python scripts/export_miniprogram_mock.py
```

脚本读取：

- `mapping_output/2026-06/enriched_transactions.csv`
- `mapping_output/2026-06/review_required.csv`

并重新生成：

- `miniprogram/mock-data/billing.js`

当前脚本固定使用 2026-06 验证数据。后续接入 API 时会移除这一固定路径。

## 本地数据契约

页面不直接访问 Mock 文件，而是统一调用 `miniprogram/services/api.js`：

- `getMonths()`
- `getSummary(month)`
- `getTransactions(params)`
- `getTransaction(id)`
- `updateTransaction(id, patch)`
- `getReviews()`
- `decideReview(id, decision)`

未来 FastAPI 只要提供等价响应，页面层不需要重写。

## 本地缓存

使用两个缓存键：

- `billing_tx_overrides_v1`：流水商户和分类修改。
- `billing_review_decisions_v1`：Mapping 确认和忽略结果。

在待确认页面点击“重置本地”可以清空这些修改。

## 当前边界

- 本地修改尚不会回写 CSV 或 Python mapping。
- 没有微信登录和双人白名单。
- 没有远程同步；两台设备的本地修改互不共享。
- 当前只有一个月份的 Mock 数据。

这些内容会在 FastAPI、数据库和微信身份接入阶段补齐。
