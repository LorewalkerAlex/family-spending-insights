import { describe, expect, it } from "vitest";

import {
  buildReviewDetailViewModel,
  buildReviewPreviewViewModel,
  decodeReviewDescriptionQuery,
  findMerchantSuggestions,
} from "../miniprogram/pages/review-detail/model";
import type {
  MappingReviewPreview,
  MappingReviewWorkspace,
  Transaction,
} from "../miniprogram/services/api";

function workspace(): MappingReviewWorkspace {
  return {
    items: [
      {
        description: "needs review",
        transaction_count: 3,
        total_amount: "60.00",
        currency: "CNY",
        latest_date: "2026-08-16",
        source_types: ["cmb_email", "manual"],
        transaction_only_exception_count: 1,
      },
    ],
    merchants: [
      { name: "Known Merchant", default_category: "餐饮美食" },
      { name: "Known Market", default_category: "日常采购" },
      { name: "Other", default_category: "其他支出" },
    ],
    categories: ["餐饮美食", "日常采购", "其他支出"],
  };
}

function transaction(id: string, date: string, amount: string, sourceType = "cmb_email"): Transaction {
  return {
    id,
    type: "expense",
    date,
    amount,
    currency: "CNY",
    source: {
      id: `source_${id}`,
      type: sourceType,
      description: "needs review",
    },
    enrichment: {
      merchant: null,
      display_name: "needs review",
      default_category: null,
      category: null,
      category_source: "unclassified",
      note: null,
      is_unclassified: true,
      review_signals: [],
    },
  };
}

function preview(overrides: Partial<MappingReviewPreview> = {}): MappingReviewPreview {
  return {
    token: "a".repeat(64),
    description: "needs review",
    merchant: "Known Merchant",
    category: "餐饮美食",
    is_new_merchant: false,
    previous_default_category: "餐饮美食",
    description_transaction_count: 3,
    description_affected_transaction_count: 3,
    default_category_affected_transaction_count: 0,
    total_affected_transaction_count: 3,
    preserved_merchant_exception_count: 1,
    preserved_category_exception_count: 0,
    ...overrides,
  };
}

describe("Review detail presentation", () => {
  it("decodes the route description without breaking literal percent text", () => {
    const description = "财付通-物语（上海）企业管理有限公司";
    expect(decodeReviewDescriptionQuery(encodeURIComponent(description))).toBe(description);
    expect(decodeReviewDescriptionQuery(description)).toBe(description);
    expect(decodeReviewDescriptionQuery("100%便利店")).toBe("100%便利店");
  });

  it("builds latest representative transactions for the selected description", () => {
    const view = buildReviewDetailViewModel(
      "needs review",
      workspace(),
      [
        transaction("old", "2026-08-10", "10"),
        transaction("new", "2026-08-16", "30", "manual"),
        transaction("middle", "2026-08-14", "20"),
        { ...transaction("other", "2026-08-17", "99"), source: { id: "s", type: "manual", description: "other" } },
      ],
    );

    expect(view).not.toBeNull();
    expect(view?.representatives.map((item) => item.id)).toEqual(["new", "middle", "old"]);
    expect(view).toMatchObject({
      transactionCountText: "3 笔",
      totalText: "-¥60.00",
      sourceText: "招商银行邮件账单 · 手工录入",
      hasExceptions: true,
    });
    expect(view?.representatives[0]).toMatchObject({
      amountText: "-¥30.00",
      dateText: "2026 年 8 月 16 日",
      sourceText: "手工录入",
    });
  });

  it("returns null when the review group has already disappeared", () => {
    expect(buildReviewDetailViewModel("missing", workspace(), [])).toBeNull();
  });

  it("suggests only exact or prefix merchant names", () => {
    expect(findMerchantSuggestions("known", workspace().merchants)).toEqual([
      { name: "Known Merchant", default_category: "餐饮美食" },
      { name: "Known Market", default_category: "日常采购" },
    ]);
    expect(findMerchantSuggestions("nothing", workspace().merchants)).toEqual([]);
  });

  it("makes new-Merchant and cross-group impact explicit", () => {
    const view = buildReviewPreviewViewModel(
      preview({
        merchant: "New Merchant",
        category: "日常采购",
        is_new_merchant: true,
        previous_default_category: null,
        default_category_affected_transaction_count: 0,
      }),
    );

    expect(view).toMatchObject({
      isNewMerchant: true,
      merchantModeText: "将创建新商户",
      changesExistingMerchantDefault: false,
      totalAffectedText: "合计影响 3 笔交易",
      preservedMerchantExceptionText: "1 笔单笔商户例外会保留",
    });
  });

  it("warns when an existing merchant default category will change", () => {
    const view = buildReviewPreviewViewModel(
      preview({
        category: "日常采购",
        previous_default_category: "餐饮美食",
        default_category_affected_transaction_count: 8,
        total_affected_transaction_count: 11,
      }),
    );

    expect(view).toMatchObject({
      changesExistingMerchantDefault: true,
      merchantModeText: "将修改已有商户默认分类",
      previousCategoryText: "餐饮美食",
      defaultCategoryAffectedText: "另有 8 笔该商户交易会随默认分类变化",
      totalAffectedText: "合计影响 11 笔交易",
    });
  });
});