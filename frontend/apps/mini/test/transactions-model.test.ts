import { describe, expect, it } from "vitest";

import {
  buildTransactionsViewModel,
  transactionMonthOptions,
} from "../miniprogram/pages/transactions/model";
import type { Transaction } from "../miniprogram/services/api";

function transaction(
  id: string,
  date: string,
  type: "income" | "expense",
  amount: string,
  options: {
    displayName?: string | null;
    merchant?: string | null;
    category?: string | null;
    isUnclassified?: boolean;
  } = {},
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
      merchant: options.merchant ?? `商户 ${id}`,
      display_name: options.displayName ?? null,
      default_category: options.category ?? "餐饮美食",
      category: options.category ?? "餐饮美食",
      category_source: options.isUnclassified ? "unclassified" : "merchant_default",
      note: null,
      is_unclassified: options.isUnclassified ?? false,
      review_signals: [],
    },
  };
}

describe("Transactions presentation model", () => {
  it("derives distinct months newest first", () => {
    expect(
      transactionMonthOptions([
        transaction("a", "2026-06-10", "expense", "10.00"),
        transaction("b", "2026-08-01", "expense", "20.00"),
        transaction("c", "2026-07-20", "income", "30.00"),
        transaction("d", "2026-08-12", "expense", "40.00"),
      ]),
    ).toEqual([
      { value: "2026-08", label: "2026 年 8 月" },
      { value: "2026-07", label: "2026 年 7 月" },
      { value: "2026-06", label: "2026 年 6 月" },
    ]);
  });

  it("defaults to the newest month, groups by date, and keeps refund direction visible", () => {
    const model = buildTransactionsViewModel([
      transaction("old", "2026-07-31", "expense", "99.00"),
      transaction("a", "2026-08-02", "expense", "12.50"),
      transaction("b", "2026-08-03", "income", "300.00"),
      transaction("refund", "2026-08-03", "expense", "-8.00"),
    ]);

    expect(model.selectedMonth).toBe("2026-08");
    expect(model.transactionCount).toBe(3);
    expect(model.groups.map((group) => group.date)).toEqual(["2026-08-03", "2026-08-02"]);
    expect(model.groups[0]?.items.map((item) => item.id)).toEqual(["refund", "b"]);
    expect(model.groups[0]?.items[0]).toMatchObject({
      amountText: "+¥8.00",
      amountTone: "positive",
    });
  });

  it("filters by month and transaction type without mutating source facts", () => {
    const transactions = [
      transaction("expense", "2026-07-10", "expense", "18.00"),
      transaction("income", "2026-07-11", "income", "500.00"),
      transaction("aug", "2026-08-01", "expense", "20.00"),
    ];

    const model = buildTransactionsViewModel(transactions, "2026-07", "expense");

    expect(model.selectedMonthLabel).toBe("2026 年 7 月");
    expect(model.filter).toBe("expense");
    expect(model.transactionCount).toBe(1);
    expect(model.groups[0]?.items[0]?.id).toBe("expense");
    expect(transactions).toHaveLength(3);
  });

  it("marks empty filter results separately from an empty ledger", () => {
    const populated = buildTransactionsViewModel(
      [transaction("expense", "2026-08-10", "expense", "20.00")],
      "2026-08",
      "income",
    );
    expect(populated).toMatchObject({
      isCompletelyEmpty: false,
      isFilteredEmpty: true,
      transactionCount: 0,
    });

    const empty = buildTransactionsViewModel([]);
    expect(empty).toMatchObject({
      isCompletelyEmpty: true,
      isFilteredEmpty: false,
      transactionCount: 0,
    });
  });
});
