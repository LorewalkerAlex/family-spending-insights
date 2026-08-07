"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  StatisticsDataError,
  TREND_MONTH_LIMIT,
  createStatisticsService,
  formatMinorUnits,
  validateStatisticsPayload,
} = require("./api.js");

function makePayload() {
  return {
    schema_version: 2,
    summary: {
      all_data: {
        total_spending_minor: 173456,
        transaction_count: 4,
        month_count: 3,
      },
      shown_data: {
        total_spending_minor: 123456,
        transaction_count: 3,
        month_count: 2,
      },
    },
    months: [
      {
        month: "2026-07",
        is_complete: false,
        show: false,
        total_spending_minor: 50000,
        transaction_count: 1,
        categories: [
          {
            category: "旅行住宿",
            spending_minor: 50000,
            transaction_count: 1,
          },
        ],
        merchants: [
          {
            merchant_name: "示例酒店",
            display_name: "示例酒店",
            is_unclassified: false,
            spending_minor: 50000,
            transaction_count: 1,
          },
        ],
      },
      {
        month: "2026-06",
        is_complete: true,
        show: true,
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
        is_complete: true,
        show: true,
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

function makeManyMonthsPayload(monthCount) {
  const months = [];
  let year = 2026;
  let month = 12;
  for (let index = 0; index < monthCount; index += 1) {
    const monthName = `${year}-${String(month).padStart(2, "0")}`;
    months.push({
      month: monthName,
      is_complete: true,
      show: true,
      total_spending_minor: 100,
      transaction_count: 1,
      categories: [
        {
          category: "餐饮美食",
          spending_minor: 100,
          transaction_count: 1,
        },
      ],
      merchants: [
        {
          merchant_name: "测试商户",
          display_name: "测试商户",
          is_unclassified: false,
          spending_minor: 100,
          transaction_count: 1,
        },
      ],
    });
    month -= 1;
    if (month === 0) {
      month = 12;
      year -= 1;
    }
  }
  return {
    schema_version: 2,
    summary: {
      all_data: {
        total_spending_minor: monthCount * 100,
        transaction_count: monthCount,
        month_count: monthCount,
      },
      shown_data: {
        total_spending_minor: monthCount * 100,
        transaction_count: monthCount,
        month_count: monthCount,
      },
    },
    months,
  };
}

function expectDataError(callback, code) {
  assert.throws(callback, (error) => {
    assert.ok(error instanceof StatisticsDataError);
    assert.equal(error.code, code);
    return true;
  });
}

function createPayloadService(payload = makePayload()) {
  const requests = [];
  const service = createStatisticsService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return {
        ok: true,
        status: 200,
        json: async () => structuredClone(payload),
      };
    },
  });
  return { requests, service };
}

test("formats integer minor units without changing cents", () => {
  assert.equal(formatMinorUnits(0), "¥0.00");
  assert.equal(formatMinorUnits(123456), "¥1,234.56");
});

test("validates schema v2, month visibility facts, and backend order", () => {
  const payload = validateStatisticsPayload(makePayload());
  assert.equal(payload.summary.all_data.month_count, 3);
  assert.equal(payload.summary.shown_data.month_count, 2);
  assert.deepEqual(
    payload.months.map((month) => month.month),
    ["2026-07", "2026-06", "2026-05"],
  );
  assert.equal(payload.months[0].is_complete, false);
  assert.equal(payload.months[0].show, false);
  assert.equal(payload.months[1].merchants[1].merchant_name, null);
  assert.ok(Object.isFrozen(payload));
});

test("keeps is_complete and show as independent public fields", () => {
  const payload = makePayload();
  payload.months[2].is_complete = false;
  const validated = validateStatisticsPayload(payload);
  assert.equal(validated.months[2].is_complete, false);
  assert.equal(validated.months[2].show, true);
});

test("accepts a valid empty statistics file", () => {
  const payload = validateStatisticsPayload({
    schema_version: 2,
    summary: {
      all_data: { total_spending_minor: 0, transaction_count: 0, month_count: 0 },
      shown_data: { total_spending_minor: 0, transaction_count: 0, month_count: 0 },
    },
    months: [],
  });
  assert.deepEqual(payload.months, []);
});

test("rejects unsupported schema versions", () => {
  const payload = makePayload();
  payload.schema_version = 1;
  expectDataError(() => validateStatisticsPayload(payload), "unsupported_schema");
});

test("rejects invalid natural month strings", () => {
  const payload = makePayload();
  payload.months[0].month = "2026-13";
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects invalid visibility field types", () => {
  const payload = makePayload();
  payload.months[0].show = "false";
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects invalid numeric field types", () => {
  const payload = makePayload();
  payload.months[1].categories[0].spending_minor = 1.5;
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects a classified merchant without merchant_name", () => {
  const payload = makePayload();
  payload.months[1].merchants[0].merchant_name = null;
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects an unclassified merchant with a formal merchant_name", () => {
  const payload = makePayload();
  payload.months[1].merchants[1].merchant_name = "不应存在";
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects duplicate months", () => {
  const payload = makePayload();
  payload.months[2].month = "2026-06";
  expectDataError(() => validateStatisticsPayload(payload), "invalid_data");
});

test("rejects month amount reconciliation failures", () => {
  const payload = makePayload();
  payload.months[1].categories[0].spending_minor = 69999;
  expectDataError(() => validateStatisticsPayload(payload), "reconciliation_error");
});

test("rejects all-data summary reconciliation failures", () => {
  const payload = makePayload();
  payload.summary.all_data.total_spending_minor -= 1;
  expectDataError(() => validateStatisticsPayload(payload), "reconciliation_error");
});

test("rejects shown-data summary reconciliation failures", () => {
  const payload = makePayload();
  payload.summary.shown_data.transaction_count += 1;
  expectDataError(() => validateStatisticsPayload(payload), "reconciliation_error");
});

test("service hides show=false months and uses shown summary", async () => {
  const { requests, service } = createPayloadService();
  const summary = await service.getSummary();
  const months = await service.getMonths();
  const june = await service.getMonthStatistics("2026-06");

  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.cache, "no-store");
  assert.match(requests[0].url, /dashboard_reload=/);
  assert.equal(summary.totalSpendingText, "¥1,234.56");
  assert.equal(summary.monthCount, 2);
  assert.deepEqual(
    months.map((month) => month.month),
    ["2026-06", "2026-05"],
  );
  assert.equal(june.categories[0].category, "餐饮美食");
  assert.equal(june.merchants[1].isUnclassified, true);
});

test("service does not expose hidden months through month detail", async () => {
  const { service } = createPayloadService();
  await assert.rejects(service.getMonthStatistics("2026-07"), (error) => {
    assert.ok(error instanceof StatisticsDataError);
    assert.equal(error.code, "month_not_found");
    return true;
  });
});

test("trend uses shown months chronologically and fills missing categories with zero", async () => {
  const { service } = createPayloadService();
  const trend = await service.getTrendStatistics();
  assert.deepEqual(
    trend.months.map((month) => month.month),
    ["2026-05", "2026-06"],
  );
  assert.deepEqual(
    trend.categories.find((item) => item.category === "餐饮美食").spendingByMonthMinor,
    [0, 70000],
  );
  assert.deepEqual(
    trend.categories.find((item) => item.category === "日常采购").spendingByMonthMinor,
    [23456, 0],
  );
});

test("trend keeps only the newest twelve shown months", async () => {
  const payload = makeManyMonthsPayload(13);
  const { service } = createPayloadService(payload);
  const months = await service.getMonths();
  const trend = await service.getTrendStatistics();

  assert.equal(months.length, 13);
  assert.equal(trend.months.length, TREND_MONTH_LIMIT);
  assert.equal(trend.months[0].month, "2026-01");
  assert.equal(trend.months.at(-1).month, "2026-12");
});

test("reloadStatistics bypasses cache and returns one coherent snapshot", async () => {
  const { requests, service } = createPayloadService();
  await service.getSummary();
  const snapshot = await service.reloadStatistics();
  assert.equal(requests.length, 2);
  assert.equal(snapshot.summary.monthCount, 2);
  assert.equal(snapshot.months.length, 2);
  assert.equal(snapshot.trend.months.length, 2);
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
