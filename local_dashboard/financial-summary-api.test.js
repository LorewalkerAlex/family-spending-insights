"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const api = require("./financial-summary-api.js");
const applicationApi = require("./application-api.js");

function payload() {
  return {
    schema_version: 1,
    summary: {
      all_data: {
        total_income_minor: 30000,
        total_spending_minor: 10000,
        net_cash_flow_minor: 20000,
        income_transaction_count: 1,
        spending_transaction_count: 2,
        month_count: 1,
      },
      shown_data: {
        total_income_minor: 30000,
        total_spending_minor: 10000,
        net_cash_flow_minor: 20000,
        income_transaction_count: 1,
        spending_transaction_count: 2,
        month_count: 1,
      },
    },
    months: [
      {
        month: "2026-01",
        spending_data_complete: true,
        show: true,
        total_income_minor: 30000,
        income_transaction_count: 1,
        total_spending_minor: 10000,
        spending_transaction_count: 2,
        net_cash_flow_minor: 20000,
      },
    ],
  };
}

test("validates income, spending, and signed cash flow", () => {
  const result = api.validateFinancialSummary(payload());
  assert.equal(result.summary.shownData.netCashFlowMinor, 20000);
  assert.equal(result.months[0].spendingDataComplete, true);
  assert.equal(api.formatMinorUnits(-12345), "-¥123.45");
});

test("rejects cash flow that does not reconcile", () => {
  const value = payload();
  value.months[0].net_cash_flow_minor = 19999;
  assert.throws(
    () => api.validateFinancialSummary(value),
    (error) => error instanceof api.FinancialSummaryDataError && error.code === "reconciliation_error",
  );
});

test("rejects shown summary that includes hidden months", () => {
  const value = payload();
  value.months[0].show = false;
  assert.throws(
    () => api.validateFinancialSummary(value),
    (error) => error instanceof api.FinancialSummaryDataError && error.code === "reconciliation_error",
  );
});

test("service loads and validates the sidecar", async () => {
  let requestedUrl = null;
  const service = api.createFinancialSummaryService({
    fetchImpl: async (url) => {
      requestedUrl = url;
      return { ok: true, status: 200, json: async () => payload() };
    },
  });
  const result = await service.load();
  assert.match(requestedUrl, /^\/data\/reports\/financial_summary\.json\?financial_reload=/);
  assert.equal(result.summary.allData.totalIncomeMinor, 30000);
});


test("application API accepts income_default enrichment without Merchant Mapping", () => {
  const transaction = applicationApi.validateTransaction({
    id: "income-1",
    type: "income",
    date: "2026-01-05",
    amount: "30000",
    currency: "CNY",
    source: { id: "manual-income-1", type: "manual", description: "工资-测试公司" },
    enrichment: {
      merchant: null,
      display_name: "工资-测试公司",
      default_category: null,
      category: "其他收入",
      category_source: "income_default",
      note: null,
      is_unclassified: false,
      review_signals: [],
    },
  });
  assert.equal(transaction.enrichment.categorySource, "income_default");
  assert.equal(transaction.enrichment.merchant, null);
});
