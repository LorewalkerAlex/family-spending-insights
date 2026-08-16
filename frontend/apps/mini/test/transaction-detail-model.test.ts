import { describe, expect, it } from "vitest";

import { buildTransactionDetailViewModel } from "../miniprogram/pages/transaction-detail/model";
import type { Transaction } from "../miniprogram/services/api";

function baseTransaction(): Transaction {
  return {
    id: "txn_detail",
    type: "expense",
    date: "2026-08-16",
    amount: "-35.60",
    currency: "CNY",
    source: {
      id: "source_detail",
      type: "cmb_email",
      description: "支付宝-测试商户",
    },
    enrichment: {
      merchant: "测试商户",
      display_name: "测试商户",
      default_category: "其他支出",
      category: "其他支出",
      category_source: "merchant_default",
      note: "退款完成",
      is_unclassified: false,
      review_signals: ["other_expense_review", "future_signal"],
    },
  };
}

describe("Transaction detail presentation model", () => {
  it("presents source, category, note, and review information without hiding unknown signals", () => {
    expect(buildTransactionDetailViewModel(baseTransaction())).toMatchObject({
      typeLabel: "支出",
      amountText: "+¥35.60",
      amountTone: "positive",
      dateText: "2026 年 8 月 16 日",
      name: "测试商户",
      merchantText: "测试商户",
      categoryText: "其他支出",
      categorySourceText: "商户默认分类",
      rawDescription: "支付宝-测试商户",
      sourceText: "招商银行邮件账单",
      hasNote: true,
      noteText: "退款完成",
      hasReviewSignals: true,
      reviewSignals: ["其他支出需要复核", "future_signal"],
    });
  });

  it("uses income defaults without inventing a merchant", () => {
    const income: Transaction = {
      ...baseTransaction(),
      type: "income",
      amount: "30000.00",
      source: {
        ...baseTransaction().source,
        type: "manual",
        description: "工资",
      },
      enrichment: {
        merchant: null,
        display_name: "工资",
        default_category: null,
        category: "其他收入",
        category_source: "income_default",
        note: null,
        is_unclassified: false,
        review_signals: [],
      },
    };

    expect(buildTransactionDetailViewModel(income)).toMatchObject({
      typeLabel: "收入",
      amountText: "+¥30,000.00",
      merchantText: "不适用",
      categoryText: "其他收入",
      categorySourceText: "收入默认分类",
      sourceText: "手工录入",
      hasNote: false,
      hasReviewSignals: false,
    });
  });
});
