import { describe, expect, it } from "vitest";

import { buildReviewListViewModel } from "../miniprogram/pages/review/model";
import type { MappingReviewWorkspace } from "../miniprogram/services/api";

function workspace(): MappingReviewWorkspace {
  return {
    items: [
      {
        description: "支付宝-待审核",
        transaction_count: 2,
        total_amount: "88.00",
        currency: "CNY",
        latest_date: "2026-08-16",
        source_types: ["cmb_email"],
        transaction_only_exception_count: 1,
      },
      {
        description: "现金早餐",
        transaction_count: 1,
        total_amount: "12.50",
        currency: "CNY",
        latest_date: "2026-08-15",
        source_types: ["manual"],
        transaction_only_exception_count: 0,
      },
    ],
    merchants: [],
    categories: ["餐饮美食"],
  };
}

describe("Review list presentation", () => {
  it("keeps Backend review order and exposes glanceable group facts", () => {
    const view = buildReviewListViewModel(workspace());

    expect(view.reviewCount).toBe(2);
    expect(view.reviewCountText).toBe("2 项待审核");
    expect(view.isEmpty).toBe(false);
    expect(view.items.map((item) => item.description)).toEqual([
      "支付宝-待审核",
      "现金早餐",
    ]);
    expect(view.items[0]).toMatchObject({
      transactionCountText: "2 笔",
      totalText: "-¥88.00",
      latestDateText: "2026 年 8 月 16 日",
      sourceText: "招商银行邮件账单",
      hasExceptions: true,
      exceptionText: "1 笔保留单笔商户例外",
    });
    expect(view.items[1]?.sourceText).toBe("手工录入");
  });

  it("builds an explicit empty state", () => {
    const view = buildReviewListViewModel({
      items: [],
      merchants: [],
      categories: [],
    });

    expect(view).toMatchObject({
      reviewCount: 0,
      reviewCountText: "暂无待审核",
      isEmpty: true,
      items: [],
    });
  });
});