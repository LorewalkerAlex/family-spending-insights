# Mail Billing Extract

本项目包含两个步骤：

1. 从 163 邮箱拉取招商银行信用卡账单 CSV。
2. 将记账 App 的自然月账单长截图与邮箱 CSV 逐笔匹配，生成可复用商户映射。

## 拉取邮箱账单

账号和授权码等参数从 `.env` 读取：

```powershell
uv run python extract_163_cmb_bills_raw_v3.py
```

脚本会跳过已经存在的账单 CSV。

## 从 App 截图生成 Mapping

自然月通常横跨两份信用卡账单。例如 2026 年 6 月交易分布在 6 月 10 日和 7 月 10 日两份账单中：

```powershell
uv run python build_mapping.py `
  --image "微信图片_20260711202903_39_5.jpg" `
  --csv `
    "cmb_bill_output/2026-06-10-cmbbilling-raw.csv" `
    "cmb_bill_output/2026-07-10-cmbbilling-raw.csv" `
  --output-dir "mapping_output/2026-06"
```

第一次执行会在本机 CPU 上运行 RapidOCR，耗时通常明显长于后续运行。OCR 结果会缓存在输出目录；相同截图再次执行会直接复用缓存。需要强制重新识别时增加 `--force-ocr`。

输出文件：

- `ocr_results.json`：OCR 原文、坐标和置信度。
- `app_transactions.csv`：从截图解析的交易。
- `matched_transactions.csv`：逐笔匹配结果和评分证据。
- `review_required.csv`：模糊匹配及两侧未匹配交易。
- `merchant_mapping.csv`：可累积复用的映射规则。
- `enriched_transactions.csv`：截图实际日期范围内的完整银行账单，应用 mapping 后补齐标准商户、账户和分类。
- `run_summary.json`：本次运行统计。

## 人工维护 Mapping

可以直接修改 `merchant_mapping.csv` 中的标准商户、账户和分类。将 `manually_confirmed` 改为 `true` 后，后续自动运行不会覆盖这条规则。

分类来源在 `category_source` 中说明：

- `keyword_rule`：根据透明的本地关键词规则推断，建议首轮检查。
- `needs_review`：没有足够依据，留空等待人工补充。

截图中的消费者姓名只保留在审核列 `consumer_ignored`，不参与匹配，也不会写入 mapping。

### 回写人工审核

在 `review_required.csv` 中填写：

- `review_decision`：填 `confirm`。
- `review_merchant`：确认后的标准商户。
- `review_account`：需要覆盖默认账户时填写。
- `review_category`：确认后的分类。
- `review_note`：可选备注。

然后把审核文件传回程序：

```powershell
uv run python build_mapping.py `
  --image "账单截图.jpg" `
  --csv "前一份账单.csv" "后一份账单.csv" `
  --output-dir "mapping_output/对应月份" `
  --review-input "mapping_output/对应月份/review_required.csv"
```

程序会先读取人工决定，再刷新审核表，因此可以直接使用原文件。确认结果会进入 mapping，并在后续月份优先参与匹配和账单补全。

## 运行测试

```powershell
uv run python -m unittest -v
```

详细设计和验收标准见 `IMPLEMENTATION_PLAN.md`。

## 微信小程序本地 Mock

无需 Docker、数据库或微信登录即可预览流水、详情编辑和 Mapping 确认交互。使用说明见 `MINIPROGRAM_LOCAL.md`，小程序项目位于 `miniprogram/`。
