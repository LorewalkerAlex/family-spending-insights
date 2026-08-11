"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  ApplicationApiError,
  DEFAULT_API_BASE,
  createApplicationService,
  findSimilarManualDescriptions,
  normalizeManualDescription,
  validateManualInputRecord,
  validateScheduledInputRule,
  validateScheduledInputRun,
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

test("Manual description matching stays lightweight and whitespace-insensitive", () => {
  assert.equal(normalizeManualDescription("  小区 门口早餐摊  "), "小区门口早餐摊");
  assert.deepEqual(
    findSimilarManualDescriptions(
      "小区门口早餐摊",
      ["小区 门口早餐摊", "小区门口水果摊", "早餐"],
    ),
    ["小区 门口早餐摊"],
  );
  assert.deepEqual(
    findSimilarManualDescriptions("早餐", ["早餐店", "公司早餐", "水果店"]),
    ["早餐店"],
  );
});

test("loads historical Manual descriptions for reuse suggestions", async () => {
  const requests = [];
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({ descriptions: ["小区门口早餐摊", "现金房租"] });
    },
  });

  const descriptions = await service.getManualDescriptions();
  assert.deepEqual(descriptions, ["小区门口早餐摊", "现金房租"]);
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/manual-descriptions`);
  assert.equal(requests[0].options.cache, "no-store");
});

test("POST sends Manual Input through the Application API contract", async () => {
  const requests = [];
  const transaction = makeTransaction({
    id: "txn-manual-1",
    date: "2026-08-09",
    amount: "88.50",
  });
  transaction.source = {
    id: "manual-1",
    type: "manual",
    description: "小区门口早餐摊",
  };
  transaction.enrichment.note = "现金";

  const service = createApplicationService({
    baseUrl: "http://127.0.0.1:9999/api/",
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({
        manual_input: {
          source_record_id: "manual-1",
          action: "created",
          transaction,
        },
      });
    },
  });

  const result = await service.createManualInput({
    type: "expense",
    date: "2026-08-09",
    amount: "88.50",
    description: "小区门口早餐摊",
    note: "现金",
  });

  assert.equal(result.sourceRecordId, "manual-1");
  assert.equal(result.action, "created");
  assert.equal(result.transaction.id, "txn-manual-1");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "http://127.0.0.1:9999/api/manual-inputs");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    type: "expense",
    date: "2026-08-09",
    amount: "88.50",
    description: "小区门口早餐摊",
    note: "现金",
  });
});

test("Manual Input client validation rejects unknown fields and numeric amounts", async () => {
  const service = createApplicationService({
    fetchImpl: async () => {
      throw new Error("fetch must not run");
    },
  });

  await assert.rejects(
    service.createManualInput({
      type: "expense",
      date: "2026-08-09",
      amount: "88.50",
      description: "测试",
      unexpected: true,
    }),
    TypeError,
  );
  await assert.rejects(
    service.createManualInput({
      type: "expense",
      date: "2026-08-09",
      amount: 88.5,
      description: "测试",
    }),
    ApplicationApiError,
  );
  await assert.rejects(
    service.createManualInput({
      type: "expense",
      date: "2026-08-09",
      amount: "88.50",
    }),
    TypeError,
  );
});

test("rejects unknown Manual Input result actions from a successful response", async () => {
  const service = createApplicationService({
    fetchImpl: async () =>
      response({
        manual_input: {
          source_record_id: "manual-1",
          action: "merged",
          transaction: makeTransaction(),
        },
      }),
  });

  await assert.rejects(
    service.createManualInput({
      type: "expense",
      date: "2026-08-09",
      amount: "88.50",
      description: "测试",
    }),
    (error) => {
      assert.ok(error instanceof ApplicationApiError);
      assert.equal(error.code, "invalid_data");
      return true;
    },
  );
});

test("validates Manual Input management records with source role and linked Transaction", () => {
  const item = validateManualInputRecord({
    source_record_id: "manual-1",
    transaction_id: "txn-1",
    source_role: "supporting",
    type: "expense",
    date: "2026-08-09",
    amount: "88.50",
    currency: "CNY",
    description: "小区门口早餐摊",
    note: "现金",
    transaction: makeTransaction(),
  });

  assert.equal(item.sourceRecordId, "manual-1");
  assert.equal(item.sourceRole, "supporting");
  assert.equal(item.transaction.id, "txn-1");
  assert.ok(Object.isFrozen(item));
});

test("loads Manual Inputs from the management endpoint", async () => {
  const requests = [];
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({
        manual_inputs: [
          {
            source_record_id: "manual-1",
            transaction_id: "txn-1",
            source_role: "authoritative",
            type: "expense",
            date: "2026-08-09",
            amount: "88.50",
            currency: "CNY",
            description: "现金早餐",
            note: null,
            transaction: makeTransaction(),
          },
        ],
      });
    },
  });

  const items = await service.getManualInputs();
  assert.equal(items.length, 1);
  assert.equal(items[0].description, "现金早餐");
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/manual-inputs`);
});

test("correction replaces Manual Source identity through a dedicated POST action", async () => {
  const requests = [];
  const correctedTransaction = makeTransaction({ id: "txn-2", date: "2026-08-10" });
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({
        manual_input_correction: {
          replaced_source_record_id: "manual-old",
          manual_input: {
            source_record_id: "manual-new",
            action: "created",
            transaction: correctedTransaction,
          },
        },
      });
    },
  });

  const result = await service.correctManualInput("manual-old", {
    type: "expense",
    date: "2026-08-10",
    amount: "90.00",
    description: "修正后的早餐",
    note: null,
  });

  assert.equal(result.replacedSourceRecordId, "manual-old");
  assert.equal(result.manualInput.sourceRecordId, "manual-new");
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/manual-inputs/manual-old/corrections`);
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    type: "expense",
    date: "2026-08-10",
    amount: "90.00",
    description: "修正后的早餐",
    note: null,
  });
});

test("deletes one Manual Source record without inventing client-side Transaction semantics", async () => {
  const requests = [];
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({
        manual_input_deletion: {
          source_record_id: "manual-1",
          transaction_id: "txn-1",
          transaction_removed: false,
        },
      });
    },
  });

  const result = await service.deleteManualInput("manual-1");
  assert.equal(result.transactionRemoved, false);
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/manual-inputs/manual-1`);
  assert.equal(requests[0].options.method, "DELETE");
});

test("validates Scheduled Input monthly rule and execution metadata", () => {
  const rule = validateScheduledInputRule({
    id: "schedule-test",
    enabled: true,
    type: "expense",
    amount: "88.50",
    currency: "CNY",
    description: "固定房租",
    note: null,
    next_date: "2026-09-28",
    last_occurrence_date: "2026-08-28",
    last_source_record_id: "manual_schedule_x_20260828",
    last_transaction_id: "txn-1",
    last_action: "created",
  });

  assert.equal(rule.nextDate, "2026-09-28");
  assert.equal(rule.lastAction, "created");
  assert.equal(rule.enabled, true);
});

test("Scheduled Input validation rejects month-end and partial execution metadata", () => {
  const base = {
    id: "schedule-test",
    enabled: true,
    type: "expense",
    amount: "88.50",
    currency: "CNY",
    description: "固定房租",
    note: null,
    next_date: "2026-09-11",
    last_occurrence_date: null,
    last_source_record_id: null,
    last_transaction_id: null,
    last_action: null,
  };

  assert.throws(
    () => validateScheduledInputRule({ ...base, next_date: "2026-08-31" }),
    /1–28/,
  );
  assert.throws(
    () =>
      validateScheduledInputRule({
        ...base,
        last_occurrence_date: "2026-08-11",
      }),
    /同时为空或同时存在/,
  );
});

test("Scheduled Input Application API supports CRUD and explicit Run Due", async () => {
  const requests = [];
  const backendRule = {
    id: "schedule-test",
    enabled: true,
    type: "expense",
    amount: "88.50",
    currency: "CNY",
    description: "固定房租",
    note: "自动录入",
    next_date: "2026-09-11",
    last_occurrence_date: null,
    last_source_record_id: null,
    last_transaction_id: null,
    last_action: null,
  };
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      if (url.endsWith("/scheduled-inputs") && options.method === "GET") {
        return response({ scheduled_inputs: [backendRule] });
      }
      if (url.endsWith("/scheduled-inputs") && options.method === "POST") {
        return response({ scheduled_input: backendRule }, { status: 201 });
      }
      if (options.method === "PATCH") {
        return response({ scheduled_input: { ...backendRule, enabled: false } });
      }
      if (options.method === "DELETE") {
        return response({ scheduled_input_deletion: { id: "schedule-test" } });
      }
      return response({
        scheduled_input_run: {
          generated_count: 1,
          occurrences: [
            {
              rule_id: "schedule-test",
              occurrence_date: "2026-08-11",
              source_record_id: "manual_schedule_x_20260811",
              transaction_id: "txn-1",
              action: "recovered",
            },
          ],
        },
      });
    },
  });

  const listed = await service.getScheduledInputs();
  const created = await service.createScheduledInput({
    type: "expense",
    amount: "88.50",
    description: "固定房租",
    note: "自动录入",
    nextDate: "2026-09-11",
    enabled: true,
  });
  const updated = await service.updateScheduledInput("schedule-test", {
    type: "expense",
    amount: "88.50",
    description: "固定房租",
    note: "自动录入",
    nextDate: "2026-09-11",
    enabled: false,
  });
  const deleted = await service.deleteScheduledInput("schedule-test");
  const run = await service.runDueScheduledInputs();

  assert.equal(listed[0].id, "schedule-test");
  assert.equal(created.id, "schedule-test");
  assert.equal(updated.enabled, false);
  assert.equal(deleted.id, "schedule-test");
  assert.equal(run.generatedCount, 1);
  assert.equal(run.occurrences[0].action, "recovered");
  assert.deepEqual(JSON.parse(requests[1].options.body), {
    type: "expense",
    amount: "88.50",
    description: "固定房租",
    note: "自动录入",
    next_date: "2026-09-11",
    enabled: true,
  });
  assert.deepEqual(
    requests.map((item) => [item.url, item.options.method]),
    [
      [`${DEFAULT_API_BASE}/scheduled-inputs`, "GET"],
      [`${DEFAULT_API_BASE}/scheduled-inputs`, "POST"],
      [`${DEFAULT_API_BASE}/scheduled-inputs/schedule-test`, "PATCH"],
      [`${DEFAULT_API_BASE}/scheduled-inputs/schedule-test`, "DELETE"],
      [`${DEFAULT_API_BASE}/scheduled-inputs/run-due`, "POST"],
    ],
  );
});

test("Scheduled Input run validation requires generated_count to match occurrences", () => {
  assert.throws(
    () =>
      validateScheduledInputRun({
        generated_count: 2,
        occurrences: [
          {
            rule_id: "schedule-test",
            occurrence_date: "2026-08-11",
            source_record_id: "manual_schedule_1",
            transaction_id: "txn-1",
            action: "created",
          },
        ],
      }),
    /不一致/,
  );
});
