"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  StatisticsDataError,
  createStatisticsService,
  formatMinorUnits,
  validateStatisticsPayload,
} = require("./api.js");

function makePayload() {
  return {
    schema_version: 1,
    summary: {
      total_spending_minor: 123456,
      transaction_count: 3,
      month_count: 2,
    },
    months: [
      {
        month: "2026-06",
        total_spending_minor: 100000,
        transaction_count: 2,
        categories: [
          {
            category: "餐饮美食",
            spending_minor: 70000,
            transaction_count: 1,
          },
          {
            category: "待分类",
            spending_minor: 30000,
            transaction_count: 1,
          },
        ],
        merchants: [
          {
            merchant_name: "示例餐厅",
            display_name: "示例餐厅",
            is_unclassified: false,
            spending_minor: 70000,
            transaction_count: 1,
          },
          {
            merchant_name: null,
            display_name: "支付平台-示例商户",
            is_unclassified: true,
            spending_minor: 30000,
            transaction_count: 1,
          },
        ],
      },
      {
        month: "2026-05",
        total_spending_minor: 23456,
        transaction_count: 1,
        categories: [
          {
            category: "日常采购",
            spending_minor: 23456,
            transaction_count: 1,
          },
        ],
        merchants: [
          {
            merchant_name: "示例超市",
            display_name: "示例超市",
            is_unclassified: false,
            spending_minor: 23456,
            transaction_count: 1,
          },
        ],
      },
    ],
  };
}

function expectDataError(callback, code) {
  assert.throws(callback, (error) => {
    assert.ok(error instanceof StatisticsDataError);
    assert.equal(error.code, code);
    return true;
  });
}

test("formats integer minor units without changing cents", () => {
  assert.equal(formatMinorUnits(0), "¥0.00");
  assert.equal(formatMinorUnits(123456), "¥1,234.56");
});

test("validates the public schema and keeps backend order", () => {
  const payload = validateStatisticsPayload(makePayload());

  assert.equal(payload.summary.month_count, 2);
  assert.deepEqual(
    payload.months.map((month) => month.month),
    ["2026-06", "2026-05"],
  );
  assert.deepEqual(
    payload.months[0].categories.map((category) => category.category),
    ["餐饮美食", "待分类"],
  );
  assert.equal(payload.months[0].merchants[1].merchant_name, null);
  assert.equal(payload.months[0].merchants[1].is_unclassified, true);
  assert.ok(Object.isFrozen(payload));
});

test("accepts a valid empty statistics file", () => {
  const payload = validateStatisticsPayload({
    schema_version: 1,
    summary: {
      total_spending_minor: 0,
      transaction_count: 0,
      month_count: 0,
    },
    months: [],
  });

  assert.deepEqual(payload.months, []);
});

test("rejects unsupported schema versions", () => {
  const payload = makePayload();
  payload.schema_version = 2;
  expectDataError(() => validateStatisticsPayload(payload), "unsupported_schema");
});

test("rejects invalid field types", () => {
  const payload = makePayload();
  payload.months[0].categories[0].spending_minor = 1.5;
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects a classified merchant without merchant_name", () => {
  const payload = makePayload();
  payload.months[0].merchants[0].merchant_name = null;
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects an unclassified merchant with a formal merchant_name", () => {
  const payload = makePayload();
  payload.months[0].merchants[1].merchant_name = "不应存在";
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects duplicate months", () => {
  const payload = makePayload();
  payload.months[1].month = "2026-06";
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects month amount reconciliation failures", () => {
  const payload = makePayload();
  payload.months[0].categories[0].spending_minor = 69999;
  expectDataError(() => validateStatisticsPayload(payload), "reconciliation_error");
});

test("rejects month transaction count reconciliation failures", () => {
  const payload = makePayload();
  payload.months[0].merchants[0].transaction_count = 2;
  expectDataError(() => validateStatisticsPayload(payload), "reconciliation_error");
});

test("rejects global amount reconciliation failures", () => {
  const payload = makePayload();
  payload.summary.total_spending_minor = 123455;
  expectDataError(() => validateStatisticsPayload(payload), "reconciliation_error");
});

test("service caches one validated request and exposes view models", async () => {
  const requests = [];
  const service = createStatisticsService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        status: 200,
        json: async () => makePayload(),
      };
    },
  });

  const summary = await service.getSummary();
  const months = await service.getMonths();
  const june = await service.getMonthStatistics("2026-06");

  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.cache, "no-store");
  assert.match(requests[0].url, /dashboard_reload=/);
  assert.equal(summary.totalSpendingText, "¥1,234.56");
  assert.deepEqual(
    months.map((month) => month.month),
    ["2026-06", "2026-05"],
  );
  assert.equal(june.categories[0].category, "餐饮美食");
  assert.equal(june.merchants[1].displayName, "支付平台-示例商户");
  assert.equal(june.merchants[1].isUnclassified, true);
});

test("reloadStatistics bypasses the cached request", async () => {
  let callCount = 0;
  const service = createStatisticsService({
    fetchImpl: async () => {
      callCount += 1;
      return {
        ok: true,
        status: 200,
        json: async () => makePayload(),
      };
    },
  });

  await service.getSummary();
  await service.reloadStatistics();
  assert.equal(callCount, 2);
});

test("service reports missing statistics files", async () => {
  const service = createStatisticsService({
    fetchImpl: async () => ({
      ok: false,
      status: 404,
      json: async () => ({}),
    }),
  });

  await assert.rejects(service.getSummary(), (error) => {
    assert.ok(error instanceof StatisticsDataError);
    assert.equal(error.code, "statistics_file_unavailable");
    return true;
  });
});

test("service reports malformed JSON", async () => {
  const service = createStatisticsService({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("bad JSON");
      },
    }),
  });

  await assert.rejects(service.getSummary(), (error) => {
    assert.ok(error instanceof StatisticsDataError);
    assert.equal(error.code, "invalid_json");
    return true;
  });
});

test("service reports unknown months", async () => {
  const service = createStatisticsService({
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => makePayload(),
    }),
  });

  await assert.rejects(service.getMonthStatistics("2024-01"), (error) => {
    assert.ok(error instanceof StatisticsDataError);
    assert.equal(error.code, "month_not_found");
    return true;
  });
});
