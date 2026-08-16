import { describe, expect, it } from "vitest";

import {
  buildHomeViewModel,
  formatMinorMoney,
  latestVisibleMonth,
  recentTransactionItems,
} from "../miniprogram/pages/home/model";
import type {
  FinancialSummary,
  MappingReviewWorkspace,
  Transaction,
} from "../miniprogram/services/api";

const summary: FinancialSummary = {
  schema_version: 1,
  summary: {
    all_data: {
      total_income_minor: 3000000,
      total_spending_minor: 725437,
      net_cash_flow_minor: 2274563,
      income_transaction_count: 1,
      spending_transaction_count: 10,
      month_count: 2,
    },
    shown_data: {
      total_income_minor: 3000000,
      total_spending_minor: 725437,
      net_cash_flow_minor: 2274563,
      income_transaction_count: 1,
      spending_transaction_count: 10,
      month_count: 1,
    },
  },
  months: [
    {
      month: "2026-06",
      spending_data_complete: true,
      show: true,
      total_income_minor: 1000000,
      income_transaction_count: 1,
      total_spending_minor: 500000,
      spending_transaction_count: 5,
      net_cash_flow_minor: 500000,
    },
    {
      month: "2026-07",
      spending_data_complete: true,
      show: true,
      total_income_minor: 3000000,
      income_transaction_count: 1,
      total_spending_minor: 725437,
      spending_transaction_count: 10,
      net_cash_flow_minor: 2274563,
    },
  ],
};

function transaction(
  id: string,
  date: string,
  type: "income" | "expense",
  amount: string,
): Transaction {
  return {
    id,
    type,
    date,
    amount,
    currency: "CNY",
    source: {
      id: `source_${id}`,
      type: "manual",
      description: `原始描述 ${id}`,
    },
    enrichment: {
      merchant: `商户 ${id}`,
      display_name: null,
      default_category: type === "income" ? "工资收入" : "餐饮美食",
      category: type === "income" ? "工资收入" : "餐饮美食",
      category_source: "merchant_default",
      note: null,
      is_unclassified: false,
      review_signals: [],
    },
  };
}

const review: MappingReviewWorkspace = {
  items: [
    {
      description: "待审核一",
      transaction_count: 2,
      total_amount: "30.00",
      currency: "CNY",
      latest_date: "2026-07-22",
      source_types: ["manual"],
      transaction_only_exception_count: 0,
    },
  ],
  merchants: [],
  categories: [],
};

describe("Home presentation model", () => {
  it("selects the newest visible month without trusting payload order", () => {
    expect(latestVisibleMonth(summary.months)?.month).toBe("2026-07");
  });

  it("formats canonical minor units with stable grouping", () => {
    expect(formatMinorMoney(725437)).toBe("¥7,254.37");
    expect(formatMinorMoney(-1250)).toBe("-¥12.50");
  });

  it("keeps only the five most recent transactions and signs amounts by type", () => {
    const items = recentTransactionItems([
      transaction("a", "2026-07-01", "expense", "10.00"),
      transaction("b", "2026-07-06", "expense", "20.00"),
      transaction("c", "2026-07-03", "income", "30.00"),
      transaction("d", "2026-07-04", "expense", "40.00"),
      transaction("e", "2026-07-05", "expense", "50.00"),
      transaction("f", "2026-07-02", "expense", "60.00"),
    ]);

    expect(items.map((item) => item.id)).toEqual(["b", "e", "d", "c", "f"]);
    expect(items[0]?.amountText).toBe("-¥20.00");
    expect(items[3]?.amountText).toBe("+¥30.00");

    const refund = recentTransactionItems([
      transaction("refund", "2026-07-07", "expense", "-12.50"),
    ]);
    expect(refund[0]?.amountText).toBe("+¥12.50");
  });

  it("builds the Home hierarchy from summary, transaction, and review queries", () => {
    const model = buildHomeViewModel(
      summary,
      [transaction("a", "2026-07-06", "expense", "20.00")],
      review,
    );

    expect(model).toMatchObject({
      hasSummary: true,
      monthLabel: "2026 年 7 月",
      spendingText: "¥7,254.37",
      incomeText: "¥30,000.00",
      netText: "¥22,745.63",
      netTone: "positive",
      reviewCount: 1,
      reviewTitle: "1 个待审核",
      isCompletelyEmpty: false,
    });
  });
});
