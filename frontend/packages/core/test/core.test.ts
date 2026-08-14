import { describe, expect, it } from "vitest";

import {
  ApiResponseError,
  FamilySpendingService,
  findSimilarManualDescriptions,
  financialSummarySchema,
  formatCnyMinorUnits,
  formatDecimalCurrency,
  toFinancialSummaryViewModel,
  toTransactionListItemViewModel,
  type HttpRequest,
  type HttpResponse,
  type HttpTransport,
  type Transaction,
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

const transactionPayload: Transaction = {
  id: "txn_1",
  type: "expense",
  date: "2026-08-13",
  amount: "88.50",
  currency: "CNY",
  source: {
    id: "manual_1",
    type: "manual",
    description: "小区门口早餐摊",
  },
  enrichment: {
    merchant: null,
    display_name: "小区门口早餐摊",
    default_category: null,
    category: "待分类",
    category_source: "unclassified",
    note: "现金",
    is_unclassified: true,
    review_signals: [],
  },
};

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

  it("formats signed CNY minor units deterministically", () => {
    expect(formatCnyMinorUnits(-10_229_307)).toBe("-¥102,293.07");
  });
});

describe("Transaction presentation", () => {
  it("keeps decimal strings precise while formatting transaction rows", () => {
    expect(formatDecimalCurrency("1234.5", "CNY")).toBe("¥1,234.50");
    const item = toTransactionListItemViewModel(transactionPayload);
    expect(item).toMatchObject({
      displayName: "小区门口早餐摊",
      amountText: "¥88.50",
      typeLabel: "支出",
      sourceLabel: "手工录入",
      isUnclassified: true,
    });
  });

  it("reuses only exact/prefix Manual descriptions and preserves source text", () => {
    expect(
      findSimilarManualDescriptions("早餐", ["早餐", "早餐摊", "周末早餐", "午餐"]),
    ).toEqual(["早餐", "早餐摊"]);
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
    expect(transport.requests).toEqual([{ method: "GET", path: "/api/financial-summary" }]);
  });

  it("captures, resolves, and reopens Feedback through the shared transport contract", async () => {
    const openFeedback = {
      id: "feedback_1",
      created_at: "2026-08-12T08:00:00.123456Z",
      status: "open",
      content: "概览金额层级不清楚",
      context: { runtime: "desktop_web", page: "/overview", workspace: "overview" },
    } as const;
    const resolvedFeedback = { ...openFeedback, status: "resolved" as const };
    const transport = new RecordingTransport(
      { status: 201, body: { feedback: openFeedback } },
      { status: 200, body: { feedback: resolvedFeedback } },
      { status: 200, body: { feedback: openFeedback } },
    );
    const service = new FamilySpendingService(transport);

    await service.createFeedback({ content: "  概览金额层级不清楚  ", context: openFeedback.context });
    await service.updateFeedbackStatus("feedback_1", "resolved");
    await service.updateFeedbackStatus("feedback_1", "open");

    expect(transport.requests[0]).toEqual({
      method: "POST",
      path: "/api/feedback",
      body: { content: "概览金额层级不清楚", context: openFeedback.context },
    });
  });

  it("covers Transaction and Manual Source lifecycle through existing backend endpoints", async () => {
    const manualRecord = {
      source_record_id: "manual_1",
      transaction_id: "txn_1",
      source_role: "authoritative",
      type: "expense",
      date: "2026-08-13",
      amount: "88.50",
      currency: "CNY",
      description: "小区门口早餐摊",
      note: "现金",
      transaction: transactionPayload,
    } as const;
    const transport = new RecordingTransport(
      { status: 200, body: { categories: ["餐饮美食"] } },
      { status: 200, body: { descriptions: ["小区门口早餐摊"] } },
      { status: 200, body: { transactions: [transactionPayload] } },
      { status: 200, body: { manual_inputs: [manualRecord] } },
      { status: 201, body: { manual_input: { source_record_id: "manual_2", action: "created", transaction: transactionPayload } } },
      { status: 200, body: { transaction: { ...transactionPayload, enrichment: { ...transactionPayload.enrichment, note: "更新" } } } },
      { status: 200, body: { manual_input_correction: { replaced_source_record_id: "manual_1", manual_input: { source_record_id: "manual_3", action: "reused", transaction: transactionPayload } } } },
      { status: 200, body: { manual_input_deletion: { source_record_id: "manual_3", transaction_id: "txn_1", transaction_removed: false } } },
    );
    const service = new FamilySpendingService(transport);

    await service.listCategories();
    await service.listManualDescriptions();
    await service.listTransactions();
    await service.listManualInputs();
    await service.createManualInput({ type: "expense", date: "2026-08-13", amount: "88.50", description: "早餐" });
    await service.updateEnrichment("txn_1", { note: "更新" });
    await service.correctManualInput("manual_1", { type: "expense", date: "2026-08-13", amount: "90", description: "早餐" });
    await service.deleteManualInput("manual_3");

    expect(transport.requests.map((request) => `${request.method} ${request.path}`)).toEqual([
      "GET /api/categories",
      "GET /api/manual-descriptions",
      "GET /api/transactions",
      "GET /api/manual-inputs",
      "POST /api/manual-inputs",
      "PATCH /api/transactions/txn_1/enrichment",
      "POST /api/manual-inputs/manual_1/corrections",
      "DELETE /api/manual-inputs/manual_3",
    ]);
  });

  it("surfaces the backend error body for a failed mutation", async () => {
    const transport = new RecordingTransport({ status: 409, body: { error: "存在多个候选交易" } });
    const service = new FamilySpendingService(transport);
    await expect(
      service.createManualInput({ type: "expense", date: "2026-08-13", amount: "88", description: "早餐" }),
    ).rejects.toMatchObject({ message: "存在多个候选交易" });
  });

  it("surfaces an unexpected HTTP status before decoding the body", async () => {
    const transport = new RecordingTransport({ status: 500, body: { error: "boom" } });
    const service = new FamilySpendingService(transport);
    await expect(service.getFinancialSummary()).rejects.toBeInstanceOf(ApiResponseError);
  });
});
