import { describe, expect, it, vi } from "vitest";

import {
  createFamilySpendingApi,
  type RequestOptions,
  type Requester,
} from "../miniprogram/services/api";

function requesterFor(
  responses: Record<string, { statusCode: number; data: unknown }>,
): { calls: RequestOptions[]; requester: Requester } {
  const calls: RequestOptions[] = [];
  const requester: Requester = (options) => {
    calls.push(options);
    const response = responses[options.url];
    if (!response) {
      options.fail({ errMsg: "request:fail missing fixture" });
      return undefined;
    }
    options.success(response);
    return undefined;
  };
  return { calls, requester: vi.fn(requester) };
}

describe("native Mini API client", () => {
  it("reads the Home V1 queries through the Canonical HTTP API", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const { calls, requester } = requesterFor({
      [`${baseUrl}/api/health`]: {
        statusCode: 200,
        data: { status: "ok" },
      },
      [`${baseUrl}/api/financial-summary`]: {
        statusCode: 200,
        data: {
          financial_summary: {
            schema_version: 1,
            summary: {
              all_data: {
                total_income_minor: 200000,
                total_spending_minor: 125000,
                net_cash_flow_minor: 75000,
                income_transaction_count: 1,
                spending_transaction_count: 4,
                month_count: 1,
              },
              shown_data: {
                total_income_minor: 200000,
                total_spending_minor: 125000,
                net_cash_flow_minor: 75000,
                income_transaction_count: 1,
                spending_transaction_count: 4,
                month_count: 1,
              },
            },
            months: [
              {
                month: "2026-07",
                spending_data_complete: true,
                show: true,
                total_income_minor: 200000,
                income_transaction_count: 1,
                total_spending_minor: 125000,
                spending_transaction_count: 4,
                net_cash_flow_minor: 75000,
              },
            ],
          },
        },
      },
      [`${baseUrl}/api/transactions`]: {
        statusCode: 200,
        data: {
          transactions: [
            {
              id: "txn_1",
              type: "expense",
              date: "2026-07-21",
              amount: "42.50",
              currency: "CNY",
              source: {
                id: "source_1",
                type: "cmb-email",
                description: "支付宝-测试商户",
              },
              enrichment: {
                merchant: "测试商户",
                display_name: "测试商户",
                default_category: "餐饮美食",
                category: "餐饮美食",
                category_source: "merchant_default",
                note: null,
                is_unclassified: false,
                review_signals: [],
              },
            },
          ],
        },
      },
      [`${baseUrl}/api/mapping-reviews`]: {
        statusCode: 200,
        data: {
          mapping_review: {
            items: [
              {
                description: "支付宝-待审核",
                transaction_count: 2,
                total_amount: "88.00",
                currency: "CNY",
                latest_date: "2026-07-20",
                source_types: ["cmb-email"],
                transaction_only_exception_count: 0,
              },
            ],
            merchants: [{ name: "测试商户", default_category: "餐饮美食" }],
            categories: ["餐饮美食"],
          },
        },
      },
    });
    const api = createFamilySpendingApi({ baseUrl: `${baseUrl}/`, requester });

    await expect(api.health()).resolves.toBeUndefined();
    await expect(api.financialSummary()).resolves.toMatchObject({
      schema_version: 1,
      months: [{ month: "2026-07", net_cash_flow_minor: 75000 }],
    });
    await expect(api.transactions()).resolves.toMatchObject([
      { id: "txn_1", enrichment: { display_name: "测试商户" } },
    ]);
    await expect(api.mappingReview()).resolves.toMatchObject({
      items: [{ description: "支付宝-待审核", transaction_count: 2 }],
    });

    expect(calls.map((call) => call.url)).toEqual([
      `${baseUrl}/api/health`,
      `${baseUrl}/api/financial-summary`,
      `${baseUrl}/api/transactions`,
      `${baseUrl}/api/mapping-reviews`,
    ]);
    expect(calls.every((call) => call.method === "GET")).toBe(true);
  });

  it("surfaces Backend HTTP failures", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const { requester } = requesterFor({
      [`${baseUrl}/api/health`]: {
        statusCode: 503,
        data: { error: "down" },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(api.health()).rejects.toThrow("HTTP 503");
  });
});
