(function attachSpendingDashboardApi(root, factory) {
  "use strict";

  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  if (root) {
    root.SpendingDashboardApi = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function createApiModule() {
  "use strict";

  const SUPPORTED_SCHEMA_VERSION = 1;
  const DEFAULT_DATA_URL = "/data/reports/spending_statistics.json";

  class StatisticsDataError extends Error {
    constructor(code, message, options = {}) {
      super(message);
      this.name = "StatisticsDataError";
      this.code = code;
      this.path = options.path || null;
      if (options.cause !== undefined) {
        this.cause = options.cause;
      }
    }
  }

  function fail(code, message, path, cause) {
    throw new StatisticsDataError(code, message, { path, cause });
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

  function requireNonEmptyString(value, path) {
    if (typeof value !== "string" || value.trim() === "") {
      fail("invalid_data", `${path} 必须是非空字符串。`, path);
    }
    return value;
  }

  function requireBoolean(value, path) {
    if (typeof value !== "boolean") {
      fail("invalid_data", `${path} 必须是布尔值。`, path);
    }
    return value;
  }

  function requireNonNegativeSafeInteger(value, path) {
    if (!Number.isSafeInteger(value) || value < 0) {
      fail("invalid_data", `${path} 必须是非负安全整数。`, path);
    }
    return value;
  }

  function addSafeInteger(total, value, path) {
    const result = total + value;
    if (!Number.isSafeInteger(result)) {
      fail("invalid_data", `${path} 的合计超出安全整数范围。`, path);
    }
    return result;
  }

  function sumBy(items, selector, path) {
    return items.reduce(
      (total, item) => addSafeInteger(total, selector(item), path),
      0,
    );
  }

  function assertEqual(actual, expected, path, label) {
    if (actual !== expected) {
      fail(
        "reconciliation_error",
        `${label}对账失败：${path} 为 ${actual}，预期为 ${expected}。`,
        path,
      );
    }
  }

  function freezeArray(items) {
    return Object.freeze(items.map((item) => Object.freeze(item)));
  }

  function validateCategory(value, monthIndex, categoryIndex) {
    const path = `months[${monthIndex}].categories[${categoryIndex}]`;
    const category = requireRecord(value, path);
    return {
      category: requireNonEmptyString(category.category, `${path}.category`),
      spending_minor: requireNonNegativeSafeInteger(
        category.spending_minor,
        `${path}.spending_minor`,
      ),
      transaction_count: requireNonNegativeSafeInteger(
        category.transaction_count,
        `${path}.transaction_count`,
      ),
    };
  }

  function validateMerchant(value, monthIndex, merchantIndex) {
    const path = `months[${monthIndex}].merchants[${merchantIndex}]`;
    const merchant = requireRecord(value, path);
    const isUnclassified = requireBoolean(
      merchant.is_unclassified,
      `${path}.is_unclassified`,
    );

    let merchantName = merchant.merchant_name;
    if (merchantName !== null) {
      merchantName = requireNonEmptyString(merchantName, `${path}.merchant_name`);
    }

    if (isUnclassified && merchantName !== null) {
      fail(
        "invalid_data",
        `${path}.merchant_name 在待分类项中必须为 null。`,
        `${path}.merchant_name`,
      );
    }

    if (!isUnclassified && merchantName === null) {
      fail(
        "invalid_data",
        `${path}.merchant_name 在已分类项中不能为空。`,
        `${path}.merchant_name`,
      );
    }

    return {
      merchant_name: merchantName,
      display_name: requireNonEmptyString(
        merchant.display_name,
        `${path}.display_name`,
      ),
      is_unclassified: isUnclassified,
      spending_minor: requireNonNegativeSafeInteger(
        merchant.spending_minor,
        `${path}.spending_minor`,
      ),
      transaction_count: requireNonNegativeSafeInteger(
        merchant.transaction_count,
        `${path}.transaction_count`,
      ),
    };
  }

  function validateMonth(value, monthIndex) {
    const path = `months[${monthIndex}]`;
    const month = requireRecord(value, path);
    const categories = requireArray(month.categories, `${path}.categories`).map(
      (category, categoryIndex) =>
        validateCategory(category, monthIndex, categoryIndex),
    );
    const merchants = requireArray(month.merchants, `${path}.merchants`).map(
      (merchant, merchantIndex) =>
        validateMerchant(merchant, monthIndex, merchantIndex),
    );

    const normalized = {
      month: requireNonEmptyString(month.month, `${path}.month`),
      total_spending_minor: requireNonNegativeSafeInteger(
        month.total_spending_minor,
        `${path}.total_spending_minor`,
      ),
      transaction_count: requireNonNegativeSafeInteger(
        month.transaction_count,
        `${path}.transaction_count`,
      ),
      categories: freezeArray(categories),
      merchants: freezeArray(merchants),
    };

    const categorySpending = sumBy(
      categories,
      (category) => category.spending_minor,
      `${path}.categories[*].spending_minor`,
    );
    const merchantSpending = sumBy(
      merchants,
      (merchant) => merchant.spending_minor,
      `${path}.merchants[*].spending_minor`,
    );
    const categoryCount = sumBy(
      categories,
      (category) => category.transaction_count,
      `${path}.categories[*].transaction_count`,
    );
    const merchantCount = sumBy(
      merchants,
      (merchant) => merchant.transaction_count,
      `${path}.merchants[*].transaction_count`,
    );

    assertEqual(
      normalized.total_spending_minor,
      categorySpending,
      `${path}.total_spending_minor`,
      "月份与分类金额",
    );
    assertEqual(
      normalized.total_spending_minor,
      merchantSpending,
      `${path}.total_spending_minor`,
      "月份与商户金额",
    );
    assertEqual(
      normalized.transaction_count,
      categoryCount,
      `${path}.transaction_count`,
      "月份与分类笔数",
    );
    assertEqual(
      normalized.transaction_count,
      merchantCount,
      `${path}.transaction_count`,
      "月份与商户笔数",
    );

    return Object.freeze(normalized);
  }

  function validateStatisticsPayload(value) {
    const payload = requireRecord(value, "root");
    const schemaVersion = requireNonNegativeSafeInteger(
      payload.schema_version,
      "schema_version",
    );

    if (schemaVersion !== SUPPORTED_SCHEMA_VERSION) {
      fail(
        "unsupported_schema",
        `不支持的统计数据版本：当前页面支持 ${SUPPORTED_SCHEMA_VERSION}，实际为 ${schemaVersion}。`,
        "schema_version",
      );
    }

    const summary = requireRecord(payload.summary, "summary");
    const normalizedSummary = Object.freeze({
      total_spending_minor: requireNonNegativeSafeInteger(
        summary.total_spending_minor,
        "summary.total_spending_minor",
      ),
      transaction_count: requireNonNegativeSafeInteger(
        summary.transaction_count,
        "summary.transaction_count",
      ),
      month_count: requireNonNegativeSafeInteger(
        summary.month_count,
        "summary.month_count",
      ),
    });

    const months = requireArray(payload.months, "months").map(validateMonth);
    const monthNames = new Set();
    months.forEach((month, index) => {
      if (monthNames.has(month.month)) {
        fail(
          "invalid_data",
          `months[${index}].month 与其他月份重复：${month.month}。`,
          `months[${index}].month`,
        );
      }
      monthNames.add(month.month);
    });

    assertEqual(
      normalizedSummary.month_count,
      months.length,
      "summary.month_count",
      "全局月份数",
    );
    assertEqual(
      normalizedSummary.total_spending_minor,
      sumBy(
        months,
        (month) => month.total_spending_minor,
        "months[*].total_spending_minor",
      ),
      "summary.total_spending_minor",
      "全局金额",
    );
    assertEqual(
      normalizedSummary.transaction_count,
      sumBy(
        months,
        (month) => month.transaction_count,
        "months[*].transaction_count",
      ),
      "summary.transaction_count",
      "全局笔数",
    );

    return Object.freeze({
      schema_version: schemaVersion,
      summary: normalizedSummary,
      months: Object.freeze(months),
    });
  }

  function formatMinorUnits(minorUnits) {
    requireNonNegativeSafeInteger(minorUnits, "minorUnits");
    const yuan = Math.floor(minorUnits / 100).toLocaleString("zh-CN");
    const cents = String(minorUnits % 100).padStart(2, "0");
    return `¥${yuan}.${cents}`;
  }

  function formatMonthLabel(month) {
    const match = /^(\d{4})-(\d{2})$/.exec(month);
    if (!match) {
      return month;
    }
    const monthNumber = Number(match[2]);
    if (monthNumber < 1 || monthNumber > 12) {
      return month;
    }
    return `${match[1]} 年 ${monthNumber} 月`;
  }

  function toSummaryViewModel(summary) {
    return Object.freeze({
      totalSpendingMinor: summary.total_spending_minor,
      totalSpendingText: formatMinorUnits(summary.total_spending_minor),
      transactionCount: summary.transaction_count,
      monthCount: summary.month_count,
    });
  }

  function toMonthSummaryViewModel(month) {
    return Object.freeze({
      month: month.month,
      monthLabel: formatMonthLabel(month.month),
      totalSpendingMinor: month.total_spending_minor,
      totalSpendingText: formatMinorUnits(month.total_spending_minor),
      transactionCount: month.transaction_count,
    });
  }

  function toMonthStatisticsViewModel(month) {
    return Object.freeze({
      ...toMonthSummaryViewModel(month),
      categories: freezeArray(
        month.categories.map((category) => ({
          category: category.category,
          spendingMinor: category.spending_minor,
          spendingText: formatMinorUnits(category.spending_minor),
          transactionCount: category.transaction_count,
        })),
      ),
      merchants: freezeArray(
        month.merchants.map((merchant) => ({
          merchantName: merchant.merchant_name,
          displayName: merchant.display_name,
          isUnclassified: merchant.is_unclassified,
          spendingMinor: merchant.spending_minor,
          spendingText: formatMinorUnits(merchant.spending_minor),
          transactionCount: merchant.transaction_count,
        })),
      ),
    });
  }

  function addCacheBuster(url, sequence) {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}dashboard_reload=${Date.now()}-${sequence}`;
  }

  function createStatisticsService(options = {}) {
    const dataUrl = options.dataUrl || DEFAULT_DATA_URL;
    const fetchImpl =
      options.fetchImpl ||
      (typeof globalThis !== "undefined" && typeof globalThis.fetch === "function"
        ? globalThis.fetch.bind(globalThis)
        : null);

    if (typeof fetchImpl !== "function") {
      throw new TypeError("createStatisticsService 需要可用的 fetch 实现。");
    }

    let cachedPayloadPromise = null;
    let requestSequence = 0;

    async function requestPayload(forceReload) {
      if (!forceReload && cachedPayloadPromise) {
        return cachedPayloadPromise;
      }

      const requestUrl = addCacheBuster(dataUrl, ++requestSequence);
      const promise = (async () => {
        let response;
        try {
          response = await fetchImpl(requestUrl, {
            cache: "no-store",
            headers: { Accept: "application/json" },
          });
        } catch (error) {
          fail(
            "statistics_file_unavailable",
            "无法读取消费统计文件。",
            dataUrl,
            error,
          );
        }

        if (!response || typeof response.ok !== "boolean") {
          fail(
            "statistics_file_unavailable",
            "统计文件请求没有返回有效响应。",
            dataUrl,
          );
        }

        if (!response.ok) {
          const status = Number.isInteger(response.status)
            ? `HTTP ${response.status}`
            : "HTTP 请求失败";
          fail(
            "statistics_file_unavailable",
            `无法读取消费统计文件（${status}）。`,
            dataUrl,
          );
        }

        let payload;
        try {
          payload = await response.json();
        } catch (error) {
          fail(
            "invalid_json",
            "消费统计文件不是有效的 JSON。",
            dataUrl,
            error,
          );
        }

        return validateStatisticsPayload(payload);
      })();

      cachedPayloadPromise = promise;
      try {
        return await promise;
      } catch (error) {
        if (cachedPayloadPromise === promise) {
          cachedPayloadPromise = null;
        }
        throw error;
      }
    }

    async function getSummary() {
      const payload = await requestPayload(false);
      return toSummaryViewModel(payload.summary);
    }

    async function getMonths() {
      const payload = await requestPayload(false);
      return Object.freeze(payload.months.map(toMonthSummaryViewModel));
    }

    async function getMonthStatistics(month) {
      requireNonEmptyString(month, "month");
      const payload = await requestPayload(false);
      const selected = payload.months.find((item) => item.month === month);
      if (!selected) {
        fail(
          "month_not_found",
          `统计数据中不存在月份 ${month}。`,
          "month",
        );
      }
      return toMonthStatisticsViewModel(selected);
    }

    async function reloadStatistics() {
      const payload = await requestPayload(true);
      return Object.freeze({
        summary: toSummaryViewModel(payload.summary),
        months: Object.freeze(payload.months.map(toMonthSummaryViewModel)),
      });
    }

    return Object.freeze({
      getSummary,
      getMonths,
      getMonthStatistics,
      reloadStatistics,
    });
  }

  return Object.freeze({
    DEFAULT_DATA_URL,
    SUPPORTED_SCHEMA_VERSION,
    StatisticsDataError,
    createStatisticsService,
    formatMinorUnits,
    validateStatisticsPayload,
  });
});
