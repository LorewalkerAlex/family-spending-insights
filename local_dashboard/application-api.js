(function attachFamilySpendingApplicationApi(root, factory) {
  "use strict";

  const api = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }

  if (root) {
    root.FamilySpendingApplicationApi = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function createApplicationApiModule() {
  "use strict";

  const DEFAULT_API_BASE = "http://127.0.0.1:8765/api";
  const CATEGORY_SOURCES = new Set([
    "merchant_default",
    "transaction_override",
    "manual_override",
    "unclassified",
  ]);

  class ApplicationApiError extends Error {
    constructor(code, message, options = {}) {
      super(message);
      this.name = "ApplicationApiError";
      this.code = code;
      this.status = options.status ?? null;
      this.path = options.path || null;
      if (options.cause !== undefined) {
        this.cause = options.cause;
      }
    }
  }

  function fail(code, message, path, options = {}) {
    throw new ApplicationApiError(code, message, { ...options, path });
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

  function requireString(value, path) {
    if (typeof value !== "string" || value.trim() === "") {
      fail("invalid_data", `${path} 必须是非空字符串。`, path);
    }
    return value;
  }

  function requireNullableString(value, path) {
    if (value === null) {
      return null;
    }
    return requireString(value, path);
  }

  function requireBoolean(value, path) {
    if (typeof value !== "boolean") {
      fail("invalid_data", `${path} 必须是布尔值。`, path);
    }
    return value;
  }

  function requireAmountString(value, path) {
    const amount = requireString(value, path);
    if (!/^-?\d+(?:\.\d+)?$/.test(amount)) {
      fail("invalid_data", `${path} 必须是十进制金额字符串。`, path);
    }
    return amount;
  }

  function freezeArray(items) {
    return Object.freeze(items.map((item) => Object.freeze(item)));
  }

  function validateTransaction(value, path = "transaction") {
    const transaction = requireRecord(value, path);
    const source = requireRecord(transaction.source, `${path}.source`);
    const enrichment = requireRecord(transaction.enrichment, `${path}.enrichment`);
    const categorySource = requireString(
      enrichment.category_source,
      `${path}.enrichment.category_source`,
    );
    if (!CATEGORY_SOURCES.has(categorySource)) {
      fail(
        "invalid_data",
        `${path}.enrichment.category_source 包含未知值 ${categorySource}。`,
        `${path}.enrichment.category_source`,
      );
    }
    const reviewSignals = requireArray(
      enrichment.review_signals,
      `${path}.enrichment.review_signals`,
    ).map((signal, index) =>
      requireString(signal, `${path}.enrichment.review_signals[${index}]`),
    );

    return Object.freeze({
      id: requireString(transaction.id, `${path}.id`),
      type: requireString(transaction.type, `${path}.type`),
      date: requireString(transaction.date, `${path}.date`),
      amount: requireAmountString(transaction.amount, `${path}.amount`),
      currency: requireString(transaction.currency, `${path}.currency`),
      source: Object.freeze({
        id: requireString(source.id, `${path}.source.id`),
        type: requireString(source.type, `${path}.source.type`),
        description: requireNullableString(
          source.description,
          `${path}.source.description`,
        ),
      }),
      enrichment: Object.freeze({
        merchant: requireNullableString(
          enrichment.merchant,
          `${path}.enrichment.merchant`,
        ),
        displayName: requireString(
          enrichment.display_name,
          `${path}.enrichment.display_name`,
        ),
        defaultCategory: requireNullableString(
          enrichment.default_category,
          `${path}.enrichment.default_category`,
        ),
        category: requireString(
          enrichment.category,
          `${path}.enrichment.category`,
        ),
        categorySource,
        note: requireNullableString(enrichment.note, `${path}.enrichment.note`),
        isUnclassified: requireBoolean(
          enrichment.is_unclassified,
          `${path}.enrichment.is_unclassified`,
        ),
        reviewSignals: Object.freeze(reviewSignals),
      }),
    });
  }

  function normalizeBaseUrl(value) {
    const base = value || DEFAULT_API_BASE;
    if (typeof base !== "string" || base.trim() === "") {
      throw new TypeError("Application API baseUrl 必须是非空字符串。");
    }
    return base.replace(/\/+$/, "");
  }

  function createApplicationService(options = {}) {
    const baseUrl = normalizeBaseUrl(options.baseUrl);
    const fetchImpl =
      options.fetchImpl ||
      (typeof globalThis !== "undefined" && typeof globalThis.fetch === "function"
        ? globalThis.fetch.bind(globalThis)
        : null);
    if (typeof fetchImpl !== "function") {
      throw new TypeError("createApplicationService 需要可用的 fetch 实现。");
    }

    async function request(path, options = {}) {
      const method = options.method || "GET";
      const requestOptions = {
        method,
        cache: "no-store",
        headers: { Accept: "application/json" },
      };
      if (Object.prototype.hasOwnProperty.call(options, "body")) {
        requestOptions.headers["Content-Type"] = "application/json";
        requestOptions.body = JSON.stringify(options.body);
      }

      let response;
      try {
        response = await fetchImpl(`${baseUrl}${path}`, requestOptions);
      } catch (error) {
        fail("api_unavailable", "无法连接本地 Family Spending API。", path, {
          cause: error,
        });
      }
      if (!response || typeof response.ok !== "boolean") {
        fail("api_unavailable", "本地 API 没有返回有效响应。", path);
      }

      let payload;
      try {
        payload = await response.json();
      } catch (error) {
        fail("invalid_json", "本地 API 返回了无法解析的 JSON。", path, {
          status: Number.isInteger(response.status) ? response.status : null,
          cause: error,
        });
      }

      if (!response.ok) {
        const record = isRecord(payload) ? payload : null;
        const backendMessage =
          record && typeof record.error === "string" && record.error.trim() !== ""
            ? record.error
            : null;
        const status = Number.isInteger(response.status) ? response.status : null;
        fail(
          "api_error",
          backendMessage || `本地 API 请求失败${status === null ? "" : `（HTTP ${status}）`}。`,
          path,
          { status },
        );
      }
      return requireRecord(payload, "root");
    }

    async function getHealth() {
      const payload = await request("/health");
      if (payload.status !== "ok") {
        fail("invalid_data", "API health 响应不是 ok。", "status");
      }
      return Object.freeze({ status: "ok" });
    }

    async function getCategories() {
      const payload = await request("/categories");
      const categories = requireArray(payload.categories, "categories").map(
        (category, index) => requireString(category, `categories[${index}]`),
      );
      return freezeArray(categories);
    }

    async function getTransactions() {
      const payload = await request("/transactions");
      const transactions = requireArray(payload.transactions, "transactions").map(
        (transaction, index) => validateTransaction(transaction, `transactions[${index}]`),
      );
      return Object.freeze(transactions);
    }

    async function getTransaction(transactionId) {
      const id = requireString(transactionId, "transactionId");
      const payload = await request(`/transactions/${encodeURIComponent(id)}`);
      return validateTransaction(payload.transaction);
    }

    async function updateEnrichment(transactionId, patch) {
      const id = requireString(transactionId, "transactionId");
      const body = requireRecord(patch, "patch");
      const allowed = new Set(["merchant", "category", "note"]);
      const keys = Object.keys(body);
      if (keys.length === 0) {
        throw new TypeError("Enrichment patch 至少需要一个字段。");
      }
      const unknown = keys.filter((key) => !allowed.has(key));
      if (unknown.length > 0) {
        throw new TypeError(`Enrichment patch 包含未知字段：${unknown.join(", ")}`);
      }
      keys.forEach((key) => {
        if (body[key] !== null && typeof body[key] !== "string") {
          throw new TypeError(`${key} 必须是字符串或 null。`);
        }
      });
      const payload = await request(
        `/transactions/${encodeURIComponent(id)}/enrichment`,
        { method: "PATCH", body },
      );
      return validateTransaction(payload.transaction);
    }

    return Object.freeze({
      getHealth,
      getCategories,
      getTransactions,
      getTransaction,
      updateEnrichment,
    });
  }

  return Object.freeze({
    ApplicationApiError,
    DEFAULT_API_BASE,
    createApplicationService,
    validateTransaction,
  });
});
