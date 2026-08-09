"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  ApplicationApiError,
  DEFAULT_API_BASE,
  createApplicationService,
  validateTransaction,
} = require("./application-api.js");

function makeTransaction(overrides = {}) {
  return {
    id: "txn-1",
    type: "expense",
    date: "2026-06-15",
    amount: "88.50",
    currency: "CNY",
    source: {
      id: "cmb-1",
      type: "cmb",
      description: "支付宝-示例商户",
    },
    enrichment: {
      merchant: "示例商户",
      display_name: "示例商户",
      default_category: "综合购物",
      category: "综合购物",
      category_source: "merchant_default",
      note: null,
      is_unclassified: false,
      review_signals: [],
    },
    ...overrides,
  };
}

function response(payload, options = {}) {
  return {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    json: async () => structuredClone(payload),
  };
}

test("validates Transaction + Enrichment without flattening source facts", () => {
  const transaction = validateTransaction(makeTransaction());
  assert.equal(transaction.id, "txn-1");
  assert.equal(transaction.source.description, "支付宝-示例商户");
  assert.equal(transaction.enrichment.displayName, "示例商户");
  assert.equal(transaction.enrichment.categorySource, "merchant_default");
  assert.ok(Object.isFrozen(transaction));
  assert.ok(Object.isFrozen(transaction.enrichment));
});

test("rejects unknown category_source values", () => {
  const transaction = makeTransaction();
  transaction.enrichment.category_source = "frontend_guess";
  assert.throws(() => validateTransaction(transaction), (error) => {
    assert.ok(error instanceof ApplicationApiError);
    assert.equal(error.code, "invalid_data");
    return true;
  });
});

test("loads formal categories and transactions from the local API", async () => {
  const requests = [];
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith("/categories")) {
        return response({ categories: ["交通出行", "综合购物"] });
      }
      return response({ transactions: [makeTransaction()] });
    },
  });

  const categories = await service.getCategories();
  const transactions = await service.getTransactions();

  assert.deepEqual(categories, ["交通出行", "综合购物"]);
  assert.equal(transactions[0].id, "txn-1");
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/categories`);
  assert.equal(requests[1].url, `${DEFAULT_API_BASE}/transactions`);
  assert.equal(requests[0].options.cache, "no-store");
});

test("PATCH sends only the caller-provided Enrichment command", async () => {
  const requests = [];
  const service = createApplicationService({
    baseUrl: "http://127.0.0.1:9999/api/",
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      const updated = makeTransaction();
      updated.enrichment.category = "交通出行";
      updated.enrichment.category_source = "manual_override";
      return response({ transaction: updated });
    },
  });

  const updated = await service.updateEnrichment("txn-1", {
    category: "交通出行",
  });

  assert.equal(updated.enrichment.category, "交通出行");
  assert.equal(requests.length, 1);
  assert.equal(
    requests[0].url,
    "http://127.0.0.1:9999/api/transactions/txn-1/enrichment",
  );
  assert.equal(requests[0].options.method, "PATCH");
  assert.equal(requests[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    category: "交通出行",
  });
});

test("PATCH allows null to reset Category to backend default semantics", async () => {
  let body = null;
  const service = createApplicationService({
    fetchImpl: async (_url, options) => {
      body = JSON.parse(options.body);
      return response({ transaction: makeTransaction() });
    },
  });

  await service.updateEnrichment("txn-1", { category: null });
  assert.deepEqual(body, { category: null });
});

test("surfaces backend Application errors without replacing their message", async () => {
  const service = createApplicationService({
    fetchImpl: async () =>
      response(
        { error: "Current Source state contains unreconciled records" },
        { ok: false, status: 409 },
      ),
  });

  await assert.rejects(service.getTransactions(), (error) => {
    assert.ok(error instanceof ApplicationApiError);
    assert.equal(error.code, "api_error");
    assert.equal(error.status, 409);
    assert.equal(error.message, "Current Source state contains unreconciled records");
    return true;
  });
});

test("reports local API connection failures separately from data failures", async () => {
  const service = createApplicationService({
    fetchImpl: async () => {
      throw new TypeError("connection refused");
    },
  });

  await assert.rejects(service.getCategories(), (error) => {
    assert.ok(error instanceof ApplicationApiError);
    assert.equal(error.code, "api_unavailable");
    return true;
  });
});

test("rejects malformed successful JSON payloads", async () => {
  const service = createApplicationService({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("bad json");
      },
    }),
  });

  await assert.rejects(service.getTransactions(), (error) => {
    assert.ok(error instanceof ApplicationApiError);
    assert.equal(error.code, "invalid_json");
    return true;
  });
});
