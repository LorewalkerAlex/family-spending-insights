import { describe, expect, it } from "vitest";

import {
  FamilySpendingService,
  spendingStatisticsSchema,
  toSpendingAnalyticsViewModel,
  type HttpRequest,
  type HttpResponse,
  type HttpTransport,
} from "../src";

const spendingStatisticsPayload = {
  schema_version: 2,
  summary: {
    all_data: {
      total_spending_minor: 75_000,
      transaction_count: 6,
      month_count: 2,
    },
    shown_data: {
      total_spending_minor: 50_000,
      transaction_count: 4,
      month_count: 1,
    },
  },
  months: [
    {
      month: "2026-02",
      is_complete: true,
      show: true,
      total_spending_minor: 50_000,
      transaction_count: 4,
      categories: [
        { category: "餐饮美食", spending_minor: 30_000, transaction_count: 2 },
        { category: "日常采购", spending_minor: 15_000, transaction_count: 1 },
        { category: "待分类", spending_minor: 5_000, transaction_count: 1 },
      ],
      merchants: [
        {
          merchant_name: "星巴克",
          display_name: "星巴克",
          is_unclassified: false,
          spending_minor: 25_000,
          transaction_count: 2,
        },
        {
          merchant_name: "盒马",
          display_name: "盒马",
          is_unclassified: false,
          spending_minor: 20_000,
          transaction_count: 1,
        },
        {
          merchant_name: null,
          display_name: "现金未知商户",
          is_unclassified: true,
          spending_minor: 5_000,
          transaction_count: 1,
        },
      ],
    },
    {
      month: "2026-01",
      is_complete: false,
      show: false,
      total_spending_minor: 25_000,
      transaction_count: 2,
      categories: [
        { category: "餐饮美食", spending_minor: 25_000, transaction_count: 2 },
      ],
      merchants: [
        {
          merchant_name: "星巴克",
          display_name: "星巴克",
          is_unclassified: false,
          spending_minor: 25_000,
          transaction_count: 2,
        },
      ],
    },
  ],
} as const;

class RecordingTransport implements HttpTransport {
  readonly requests: HttpRequest[] = [];

  constructor(private readonly response: HttpResponse) {}

  async request(request: HttpRequest): Promise<HttpResponse> {
    this.requests.push(request);
    return this.response;
  }
}

describe("Spending Statistics contract", () => {
  it("validates monthly category/merchant reconciliation and builds display-only shares", () => {
    const decoded = spendingStatisticsSchema.parse(spendingStatisticsPayload);
    const view = toSpendingAnalyticsViewModel(decoded);

    expect(view.selectedMonth).toBe("2026-02");
    expect(view.totalSpendingText).toBe("¥500.00");
    expect(view.transactionCount).toBe(4);
    expect(view.monthOptions.map((item) => item.month)).toEqual(["2026-02"]);
    expect(view.categories.map((item) => [item.category, item.shareText])).toEqual([
      ["餐饮美食", "60.0%"],
      ["日常采购", "30.0%"],
      ["待分类", "10.0%"],
    ]);
    expect(view.topMerchants.map((item) => item.displayName)).toEqual([
      "星巴克",
      "盒马",
      "现金未知商户",
    ]);
    expect(view.topMerchants[2]?.isUnclassified).toBe(true);
  });

  it("rejects a projection whose category or summary totals no longer reconcile", () => {
    const badCategory = structuredClone(spendingStatisticsPayload);
    badCategory.months[0].categories[0].spending_minor = 29_999;
    expect(() => spendingStatisticsSchema.parse(badCategory)).toThrow();

    const badSummary = structuredClone(spendingStatisticsPayload);
    badSummary.summary.shown_data.total_spending_minor = 49_999;
    expect(() => spendingStatisticsSchema.parse(badSummary)).toThrow();
  });

  it("falls back to the newest visible month and validates merchant limits", () => {
    const decoded = spendingStatisticsSchema.parse(spendingStatisticsPayload);
    expect(toSpendingAnalyticsViewModel(decoded, "2026-01").selectedMonth).toBe("2026-02");
    expect(() => toSpendingAnalyticsViewModel(decoded, undefined, 0)).toThrow(TypeError);
  });
});

describe("FamilySpendingService Spending Statistics", () => {
  it("uses the canonical read-only Spending Statistics endpoint", async () => {
    const transport = new RecordingTransport({
      status: 200,
      body: { spending_statistics: spendingStatisticsPayload },
    });
    const service = new FamilySpendingService(transport);

    await expect(service.getSpendingStatistics()).resolves.toEqual(spendingStatisticsPayload);
    expect(transport.requests).toEqual([
      { method: "GET", path: "/api/spending-statistics" },
    ]);
  });
});
