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
  it("reads health and financial summary through the Canonical HTTP API", async () => {
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
    });
    const api = createFamilySpendingApi({ baseUrl: `${baseUrl}/`, requester });

    await expect(api.health()).resolves.toBeUndefined();
    await expect(api.financialSummary()).resolves.toMatchObject({
      schema_version: 1,
      months: [{ month: "2026-07", net_cash_flow_minor: 75000 }],
    });

    expect(calls.map((call) => call.url)).toEqual([
      `${baseUrl}/api/health`,
      `${baseUrl}/api/financial-summary`,
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
