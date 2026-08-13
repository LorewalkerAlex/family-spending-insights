import { describe, expect, it } from "vitest";

import {
  ApiResponseError,
  FamilySpendingService,
  financialSummarySchema,
  formatCnyMinorUnits,
  toFinancialSummaryViewModel,
  type HttpRequest,
  type HttpResponse,
  type HttpTransport,
} from "../src";

const financialSummaryPayload = {
  schema_version: 1,
  summary: {
    all_data: {
      total_income_minor: 200_000,
      total_spending_minor: 125_000,
      net_cash_flow_minor: 75_000,
      income_transaction_count: 2,
      spending_transaction_count: 4,
      month_count: 2,
    },
    shown_data: {
      total_income_minor: 120_000,
      total_spending_minor: 50_000,
      net_cash_flow_minor: 70_000,
      income_transaction_count: 1,
      spending_transaction_count: 2,
      month_count: 1,
    },
  },
  months: [
    {
      month: "2026-02",
      spending_data_complete: true,
      show: true,
      total_income_minor: 120_000,
      income_transaction_count: 1,
      total_spending_minor: 50_000,
      spending_transaction_count: 2,
      net_cash_flow_minor: 70_000,
    },
    {
      month: "2026-01",
      spending_data_complete: false,
      show: false,
      total_income_minor: 80_000,
      income_transaction_count: 1,
      total_spending_minor: 75_000,
      spending_transaction_count: 2,
      net_cash_flow_minor: 5_000,
    },
  ],
} as const;

class RecordingTransport implements HttpTransport {
  readonly requests: HttpRequest[] = [];
  private readonly responses: HttpResponse[];

  constructor(...responses: HttpResponse[]) {
    this.responses = [...responses];
  }

  async request(request: HttpRequest): Promise<HttpResponse> {
    this.requests.push(request);
    const response = this.responses.shift();
    if (!response) {
      throw new Error("No mock response configured");
    }
    return response;
  }
}

describe("Financial Summary contract", () => {
  it("validates backend reconciliation and builds the shown-data hero without recomputing facts", () => {
    const decoded = financialSummarySchema.parse(financialSummaryPayload);
    const viewModel = toFinancialSummaryViewModel(decoded);

    expect(viewModel.hero.netCashFlowText).toBe("¥700.00");
    expect(viewModel.hero.totalIncomeText).toBe("¥1,200.00");
    expect(viewModel.hero.totalSpendingText).toBe("¥500.00");
    expect(viewModel.hero.monthCount).toBe(1);
    expect(viewModel.visibleMonths.map((month) => month.month)).toEqual(["2026-02"]);
  });

  it("rejects a projection whose aggregate no longer reconciles with its month rows", () => {
    const invalid = structuredClone(financialSummaryPayload);
    invalid.summary.shown_data.total_spending_minor = 49_999;
    invalid.summary.shown_data.net_cash_flow_minor = 70_001;

    expect(() => financialSummarySchema.parse(invalid)).toThrow();
  });

  it("rejects month accumulation outside JavaScript's safe integer range", () => {
    const unsafe = {
      schema_version: 1,
      summary: {
        all_data: {
          total_income_minor: Number.MAX_SAFE_INTEGER,
          total_spending_minor: 0,
          net_cash_flow_minor: Number.MAX_SAFE_INTEGER,
          income_transaction_count: 2,
          spending_transaction_count: 0,
          month_count: 2,
        },
        shown_data: {
          total_income_minor: Number.MAX_SAFE_INTEGER,
          total_spending_minor: 0,
          net_cash_flow_minor: Number.MAX_SAFE_INTEGER,
          income_transaction_count: 2,
          spending_transaction_count: 0,
          month_count: 2,
        },
      },
      months: [
        {
          month: "2026-02",
          spending_data_complete: true,
          show: true,
          total_income_minor: Number.MAX_SAFE_INTEGER,
          income_transaction_count: 1,
          total_spending_minor: 0,
          spending_transaction_count: 0,
          net_cash_flow_minor: Number.MAX_SAFE_INTEGER,
        },
        {
          month: "2026-01",
          spending_data_complete: true,
          show: true,
          total_income_minor: 1,
          income_transaction_count: 1,
          total_spending_minor: 0,
          spending_transaction_count: 0,
          net_cash_flow_minor: 1,
        },
      ],
    };

    const result = financialSummarySchema.safeParse(unsafe);
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((issue) => issue.message.includes("safe integer range"))).toBe(true);
    }
  });

  it("formats signed CNY minor units deterministically", () => {
    expect(formatCnyMinorUnits(-10_229_307)).toBe("-¥102,293.07");
  });
});

describe("FamilySpendingService", () => {
  it("uses the formal Financial Summary API boundary", async () => {
    const transport = new RecordingTransport({
      status: 200,
      body: { financial_summary: financialSummaryPayload },
    });
    const service = new FamilySpendingService(transport);

    await expect(service.getFinancialSummary()).resolves.toEqual(financialSummaryPayload);
    expect(transport.requests).toEqual([
      { method: "GET", path: "/api/financial-summary" },
    ]);
  });

  it("captures, resolves, and reopens Feedback through the shared transport contract", async () => {
    const openFeedback = {
      id: "feedback_1",
      created_at: "2026-08-12T08:00:00.123456Z",
      status: "open",
      content: "概览金额层级不清楚",
      context: {
        runtime: "desktop_web",
        page: "/overview",
        workspace: "overview",
      },
    } as const;
    const resolvedFeedback = { ...openFeedback, status: "resolved" as const };
    const transport = new RecordingTransport(
      { status: 201, body: { feedback: openFeedback } },
      { status: 200, body: { feedback: resolvedFeedback } },
      { status: 200, body: { feedback: openFeedback } },
    );
    const service = new FamilySpendingService(transport);

    await service.createFeedback({
      content: "  概览金额层级不清楚  ",
      context: {
        runtime: "desktop_web",
        page: "/overview",
        workspace: "overview",
      },
    });
    await service.updateFeedbackStatus("feedback_1", "resolved");
    await service.updateFeedbackStatus("feedback_1", "open");

    expect(transport.requests).toEqual([
      {
        method: "POST",
        path: "/api/feedback",
        body: {
          content: "概览金额层级不清楚",
          context: {
            runtime: "desktop_web",
            page: "/overview",
            workspace: "overview",
          },
        },
      },
      {
        method: "PATCH",
        path: "/api/feedback/feedback_1",
        body: { status: "resolved" },
      },
      {
        method: "PATCH",
        path: "/api/feedback/feedback_1",
        body: { status: "open" },
      },
    ]);
  });

  it("surfaces an unexpected HTTP status before decoding the body", async () => {
    const transport = new RecordingTransport({ status: 500, body: { error: "boom" } });
    const service = new FamilySpendingService(transport);

    await expect(service.getFinancialSummary()).rejects.toBeInstanceOf(ApiResponseError);
  });
});
