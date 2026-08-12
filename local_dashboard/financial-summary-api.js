(function attachFinancialSummaryApi(root, factory) {
  "use strict";

  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  if (root) {
    root.FamilySpendingFinancialApi = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function createFinancialSummaryApi() {
  "use strict";

  const SUPPORTED_SCHEMA_VERSION = 1;
  const DEFAULT_DATA_URL = "/data/reports/financial_summary.json";

  class FinancialSummaryDataError extends Error {
    constructor(code, message, options = {}) {
      super(message);
      this.name = "FinancialSummaryDataError";
      this.code = code;
      this.path = options.path || null;
      if (options.cause !== undefined) {
        this.cause = options.cause;
      }
    }
  }

  function fail(code, message, path, cause) {
    throw new FinancialSummaryDataError(code, message, { path, cause });
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function requireRecord(value, path) {
    if (!isRecord(value)) {
      fail("invalid_data", `${path} 必须是对象。`, path);
    }
    return value;
  }

  function requireArray(value, path) {
    if (!Array.isArray(value)) {
      fail("invalid_data", `${path} 必须是数组。`, path);
    }
    return value;
  }

  function requireSafeInteger(value, path) {
    if (!Number.isSafeInteger(value)) {
      fail("invalid_data", `${path} 必须是安全整数。`, path);
    }
    return value;
  }

  function requireNonNegativeSafeInteger(value, path) {
    const integer = requireSafeInteger(value, path);
    if (integer < 0) {
      fail("invalid_data", `${path} 必须是非负整数。`, path);
    }
    return integer;
  }

  function requireBoolean(value, path) {
    if (typeof value !== "boolean") {
      fail("invalid_data", `${path} 必须是布尔值。`, path);
    }
    return value;
  }

  function requireMonth(value, path) {
    if (typeof value !== "string" || !/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) {
      fail("invalid_data", `${path} 必须使用 YYYY-MM 格式。`, path);
    }
    return value;
  }

  function addSafeInteger(total, value, path) {
    const next = total + value;
    if (!Number.isSafeInteger(next)) {
      fail("invalid_data", `${path} 合计超出安全整数范围。`, path);
    }
    return next;
  }

  function validateAggregate(value, path) {
    const item = requireRecord(value, path);
    const totalIncomeMinor = requireNonNegativeSafeInteger(
      item.total_income_minor,
      `${path}.total_income_minor`,
    );
    const totalSpendingMinor = requireNonNegativeSafeInteger(
      item.total_spending_minor,
      `${path}.total_spending_minor`,
    );
    const netCashFlowMinor = requireSafeInteger(
      item.net_cash_flow_minor,
      `${path}.net_cash_flow_minor`,
    );
    if (netCashFlowMinor !== totalIncomeMinor - totalSpendingMinor) {
      fail(
        "reconciliation_error",
        `${path}.net_cash_flow_minor 必须等于收入减净消费。`,
        `${path}.net_cash_flow_minor`,
      );
    }
    return Object.freeze({
      totalIncomeMinor,
      totalSpendingMinor,
      netCashFlowMinor,
      incomeTransactionCount: requireNonNegativeSafeInteger(
        item.income_transaction_count,
        `${path}.income_transaction_count`,
      ),
      spendingTransactionCount: requireNonNegativeSafeInteger(
        item.spending_transaction_count,
        `${path}.spending_transaction_count`,
      ),
      monthCount: requireNonNegativeSafeInteger(item.month_count, `${path}.month_count`),
    });
  }

  function validateMonth(value, index) {
    const path = `months[${index}]`;
    const item = requireRecord(value, path);
    const totalIncomeMinor = requireNonNegativeSafeInteger(
      item.total_income_minor,
      `${path}.total_income_minor`,
    );
    const totalSpendingMinor = requireNonNegativeSafeInteger(
      item.total_spending_minor,
      `${path}.total_spending_minor`,
    );
    const netCashFlowMinor = requireSafeInteger(
      item.net_cash_flow_minor,
      `${path}.net_cash_flow_minor`,
    );
    if (netCashFlowMinor !== totalIncomeMinor - totalSpendingMinor) {
      fail(
        "reconciliation_error",
        `${path}.net_cash_flow_minor 必须等于收入减净消费。`,
        `${path}.net_cash_flow_minor`,
      );
    }
    return Object.freeze({
      month: requireMonth(item.month, `${path}.month`),
      spendingDataComplete: requireBoolean(
        item.spending_data_complete,
        `${path}.spending_data_complete`,
      ),
      show: requireBoolean(item.show, `${path}.show`),
      totalIncomeMinor,
      incomeTransactionCount: requireNonNegativeSafeInteger(
        item.income_transaction_count,
        `${path}.income_transaction_count`,
      ),
      totalSpendingMinor,
      spendingTransactionCount: requireNonNegativeSafeInteger(
        item.spending_transaction_count,
        `${path}.spending_transaction_count`,
      ),
      netCashFlowMinor,
    });
  }

  function reconcileAggregate(summary, months, path) {
    if (summary.monthCount !== months.length) {
      fail("reconciliation_error", `${path}.month_count 与月份数量不一致。`, `${path}.month_count`);
    }
    const totals = months.reduce(
      (accumulator, month) => ({
        income: addSafeInteger(accumulator.income, month.totalIncomeMinor, path),
        spending: addSafeInteger(accumulator.spending, month.totalSpendingMinor, path),
        incomeCount: addSafeInteger(
          accumulator.incomeCount,
          month.incomeTransactionCount,
          path,
        ),
        spendingCount: addSafeInteger(
          accumulator.spendingCount,
          month.spendingTransactionCount,
          path,
        ),
      }),
      { income: 0, spending: 0, incomeCount: 0, spendingCount: 0 },
    );
    if (
      summary.totalIncomeMinor !== totals.income ||
      summary.totalSpendingMinor !== totals.spending ||
      summary.incomeTransactionCount !== totals.incomeCount ||
      summary.spendingTransactionCount !== totals.spendingCount
    ) {
      fail("reconciliation_error", `${path} 与月份明细对账失败。`, path);
    }
  }

  function validateFinancialSummary(value) {
    const payload = requireRecord(value, "root");
    const version = requireNonNegativeSafeInteger(payload.schema_version, "schema_version");
    if (version !== SUPPORTED_SCHEMA_VERSION) {
      fail(
        "unsupported_schema",
        `不支持的家庭财务摘要版本：当前页面支持 ${SUPPORTED_SCHEMA_VERSION}，实际为 ${version}。`,
        "schema_version",
      );
    }
    const summary = requireRecord(payload.summary, "summary");
    const allData = validateAggregate(summary.all_data, "summary.all_data");
    const shownData = validateAggregate(summary.shown_data, "summary.shown_data");
    const months = requireArray(payload.months, "months").map(validateMonth);
    const names = new Set();
    months.forEach((month, index) => {
      if (names.has(month.month)) {
        fail("invalid_data", `months[${index}].month 重复：${month.month}。`, `months[${index}].month`);
      }
      names.add(month.month);
    });
    reconcileAggregate(allData, months, "summary.all_data");
    reconcileAggregate(
      shownData,
      months.filter((month) => month.show),
      "summary.shown_data",
    );
    return Object.freeze({
      schemaVersion: version,
      summary: Object.freeze({ allData, shownData }),
      months: Object.freeze(months),
    });
  }

  function formatMinorUnits(value) {
    requireSafeInteger(value, "minorUnits");
    const sign = value < 0 ? "-" : "";
    const absolute = Math.abs(value);
    const yuan = Math.floor(absolute / 100).toLocaleString("zh-CN");
    const cents = String(absolute % 100).padStart(2, "0");
    return `${sign}¥${yuan}.${cents}`;
  }

  function addCacheBuster(url, sequence) {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}financial_reload=${Date.now()}-${sequence}`;
  }

  function createFinancialSummaryService(options = {}) {
    const dataUrl = options.dataUrl || DEFAULT_DATA_URL;
    const fetchImpl =
      options.fetchImpl ||
      (typeof globalThis !== "undefined" && typeof globalThis.fetch === "function"
        ? globalThis.fetch.bind(globalThis)
        : null);
    if (typeof fetchImpl !== "function") {
      throw new TypeError("createFinancialSummaryService 需要可用的 fetch 实现。");
    }
    let sequence = 0;

    async function load() {
      sequence += 1;
      let response;
      try {
        response = await fetchImpl(addCacheBuster(dataUrl, sequence), { cache: "no-store" });
      } catch (error) {
        fail("unavailable", "无法读取家庭财务摘要。", dataUrl, error);
      }
      if (!response || !response.ok) {
        const status = response && Number.isInteger(response.status) ? response.status : null;
        fail(
          "unavailable",
          `家庭财务摘要请求失败${status === null ? "" : `（HTTP ${status}）`}。`,
          dataUrl,
        );
      }
      let payload;
      try {
        payload = await response.json();
      } catch (error) {
        fail("invalid_json", "家庭财务摘要不是有效 JSON。", dataUrl, error);
      }
      return validateFinancialSummary(payload);
    }

    return Object.freeze({ load });
  }

  return Object.freeze({
    DEFAULT_DATA_URL,
    SUPPORTED_SCHEMA_VERSION,
    FinancialSummaryDataError,
    validateFinancialSummary,
    formatMinorUnits,
    createFinancialSummaryService,
  });
});
