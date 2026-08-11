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
  const MANUAL_INPUT_ACTIONS = new Set(["created", "matched", "reused"]);
  const SCHEDULED_RUN_ACTIONS = new Set(["created", "matched", "reused", "recovered"]);
  const SOURCE_ROLES = new Set(["authoritative", "supporting"]);

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

  function requireNonNegativeInteger(value, path) {
    if (!Number.isInteger(value) || value < 0) {
      fail("invalid_data", `${path} 必须是非负整数。`, path);
    }
    return value;
  }

  function requirePositiveInteger(value, path) {
    if (!Number.isInteger(value) || value <= 0) {
      fail("invalid_data", `${path} 必须是正整数。`, path);
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

  function requireScheduledDate(value, path) {
    const text = requireString(value, path);
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    if (!match) {
      fail("invalid_data", `${path} 必须使用 YYYY-MM-DD。`, path);
    }
    const day = Number(match[3]);
    if (day < 1 || day > 28) {
      fail("invalid_data", `${path} 的月度日期必须在 1–28 日。`, path);
    }
    return text;
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

  function validateManualInputResult(value, path = "manual_input") {
    const result = requireRecord(value, path);
    const action = requireString(result.action, `${path}.action`);
    if (!MANUAL_INPUT_ACTIONS.has(action)) {
      fail(
        "invalid_data",
        `${path}.action 包含未知值 ${action}。`,
        `${path}.action`,
      );
    }
    return Object.freeze({
      sourceRecordId: requireString(
        result.source_record_id,
        `${path}.source_record_id`,
      ),
      action,
      transaction: validateTransaction(result.transaction, `${path}.transaction`),
    });
  }

  function validateManualInputRecord(value, path = "manual_inputs[]") {
    const item = requireRecord(value, path);
    const role = requireString(item.source_role, `${path}.source_role`);
    if (!SOURCE_ROLES.has(role)) {
      fail("invalid_data", `${path}.source_role 包含未知值 ${role}。`, `${path}.source_role`);
    }
    const type = requireString(item.type, `${path}.type`);
    if (type !== "income" && type !== "expense") {
      fail("invalid_data", `${path}.type 必须是 income 或 expense。`, `${path}.type`);
    }
    return Object.freeze({
      sourceRecordId: requireString(item.source_record_id, `${path}.source_record_id`),
      transactionId: requireString(item.transaction_id, `${path}.transaction_id`),
      sourceRole: role,
      type,
      date: requireString(item.date, `${path}.date`),
      amount: requireAmountString(item.amount, `${path}.amount`),
      currency: requireString(item.currency, `${path}.currency`),
      description: requireNullableString(item.description, `${path}.description`),
      note: requireNullableString(item.note, `${path}.note`),
      transaction: validateTransaction(item.transaction, `${path}.transaction`),
    });
  }

  function validateManualInputCorrection(value, path = "manual_input_correction") {
    const correction = requireRecord(value, path);
    return Object.freeze({
      replacedSourceRecordId: requireString(
        correction.replaced_source_record_id,
        `${path}.replaced_source_record_id`,
      ),
      manualInput: validateManualInputResult(
        correction.manual_input,
        `${path}.manual_input`,
      ),
    });
  }

  function validateManualInputDeletion(value, path = "manual_input_deletion") {
    const deletion = requireRecord(value, path);
    return Object.freeze({
      sourceRecordId: requireString(
        deletion.source_record_id,
        `${path}.source_record_id`,
      ),
      transactionId: requireString(
        deletion.transaction_id,
        `${path}.transaction_id`,
      ),
      transactionRemoved: requireBoolean(
        deletion.transaction_removed,
        `${path}.transaction_removed`,
      ),
    });
  }

  function validateScheduledInputRule(value, path = "scheduled_input") {
    const rule = requireRecord(value, path);
    const type = requireString(rule.type, `${path}.type`);
    if (type !== "income" && type !== "expense") {
      fail("invalid_data", `${path}.type 必须是 income 或 expense。`, `${path}.type`);
    }
    const lastAction = requireNullableString(rule.last_action, `${path}.last_action`);
    if (lastAction !== null && !SCHEDULED_RUN_ACTIONS.has(lastAction)) {
      fail(
        "invalid_data",
        `${path}.last_action 包含未知值 ${lastAction}。`,
        `${path}.last_action`,
      );
    }
    const lastFields = [
      rule.last_occurrence_date,
      rule.last_source_record_id,
      rule.last_transaction_id,
      rule.last_action,
    ];
    const presentCount = lastFields.filter((value) => value !== null).length;
    if (presentCount !== 0 && presentCount !== lastFields.length) {
      fail("invalid_data", `${path} 的最近执行字段必须同时为空或同时存在。`, path);
    }
    return Object.freeze({
      id: requireString(rule.id, `${path}.id`),
      enabled: requireBoolean(rule.enabled, `${path}.enabled`),
      type,
      amount: requireAmountString(rule.amount, `${path}.amount`),
      currency: requireString(rule.currency, `${path}.currency`),
      description: requireString(rule.description, `${path}.description`),
      note: requireNullableString(rule.note, `${path}.note`),
      nextDate: requireScheduledDate(rule.next_date, `${path}.next_date`),
      lastOccurrenceDate:
        rule.last_occurrence_date === null
          ? null
          : requireScheduledDate(
              rule.last_occurrence_date,
              `${path}.last_occurrence_date`,
            ),
      lastSourceRecordId: requireNullableString(
        rule.last_source_record_id,
        `${path}.last_source_record_id`,
      ),
      lastTransactionId: requireNullableString(
        rule.last_transaction_id,
        `${path}.last_transaction_id`,
      ),
      lastAction,
    });
  }

  function validateScheduledInputRun(value, path = "scheduled_input_run") {
    const run = requireRecord(value, path);
    const occurrences = requireArray(run.occurrences, `${path}.occurrences`).map(
      (item, index) => {
        const occurrencePath = `${path}.occurrences[${index}]`;
        const record = requireRecord(item, occurrencePath);
        const action = requireString(record.action, `${occurrencePath}.action`);
        if (!SCHEDULED_RUN_ACTIONS.has(action)) {
          fail(
            "invalid_data",
            `${occurrencePath}.action 包含未知值 ${action}。`,
            `${occurrencePath}.action`,
          );
        }
        return Object.freeze({
          ruleId: requireString(record.rule_id, `${occurrencePath}.rule_id`),
          occurrenceDate: requireScheduledDate(
            record.occurrence_date,
            `${occurrencePath}.occurrence_date`,
          ),
          sourceRecordId: requireString(
            record.source_record_id,
            `${occurrencePath}.source_record_id`,
          ),
          transactionId: requireString(
            record.transaction_id,
            `${occurrencePath}.transaction_id`,
          ),
          action,
        });
      },
    );
    const generatedCount = requireNonNegativeInteger(
      run.generated_count,
      `${path}.generated_count`,
    );
    if (generatedCount !== occurrences.length) {
      fail(
        "invalid_data",
        `${path}.generated_count 与 occurrences 数量不一致。`,
        `${path}.generated_count`,
      );
    }
    return Object.freeze({
      generatedCount,
      occurrences: Object.freeze(occurrences),
    });
  }

  function validateMappingReviewItem(value, path = "mapping_review.items[]") {
    const item = requireRecord(value, path);
    const sourceTypes = requireArray(item.source_types, `${path}.source_types`).map(
      (sourceType, index) => requireString(sourceType, `${path}.source_types[${index}]`),
    );
    return Object.freeze({
      description: requireString(item.description, `${path}.description`),
      transactionCount: requirePositiveInteger(
        item.transaction_count,
        `${path}.transaction_count`,
      ),
      totalAmount: requireAmountString(item.total_amount, `${path}.total_amount`),
      currency: requireString(item.currency, `${path}.currency`),
      latestDate: requireString(item.latest_date, `${path}.latest_date`),
      sourceTypes: Object.freeze(sourceTypes),
      transactionOnlyExceptionCount: requireNonNegativeInteger(
        item.transaction_only_exception_count,
        `${path}.transaction_only_exception_count`,
      ),
    });
  }

  function validateMerchantMappingOption(value, path = "mapping_review.merchants[]") {
    const merchant = requireRecord(value, path);
    return Object.freeze({
      name: requireString(merchant.name, `${path}.name`),
      defaultCategory: requireString(
        merchant.default_category,
        `${path}.default_category`,
      ),
    });
  }

  function validateMappingReviewWorkspace(value, path = "mapping_review") {
    const workspace = requireRecord(value, path);
    const items = requireArray(workspace.items, `${path}.items`).map((item, index) =>
      validateMappingReviewItem(item, `${path}.items[${index}]`),
    );
    const merchants = requireArray(workspace.merchants, `${path}.merchants`).map(
      (merchant, index) =>
        validateMerchantMappingOption(merchant, `${path}.merchants[${index}]`),
    );
    const categories = requireArray(workspace.categories, `${path}.categories`).map(
      (category, index) => requireString(category, `${path}.categories[${index}]`),
    );
    return Object.freeze({
      items: Object.freeze(items),
      merchants: Object.freeze(merchants),
      categories: Object.freeze(categories),
    });
  }

  function validateMappingReviewPreview(value, path = "preview") {
    const preview = requireRecord(value, path);
    const token = requireString(preview.token, `${path}.token`);
    if (!/^[0-9a-f]{64}$/.test(token)) {
      fail("invalid_data", `${path}.token 必须是 SHA-256 十六进制字符串。`, `${path}.token`);
    }
    return Object.freeze({
      token,
      description: requireString(preview.description, `${path}.description`),
      merchant: requireString(preview.merchant, `${path}.merchant`),
      category: requireString(preview.category, `${path}.category`),
      isNewMerchant: requireBoolean(
        preview.is_new_merchant,
        `${path}.is_new_merchant`,
      ),
      previousDefaultCategory: requireNullableString(
        preview.previous_default_category,
        `${path}.previous_default_category`,
      ),
      descriptionTransactionCount: requirePositiveInteger(
        preview.description_transaction_count,
        `${path}.description_transaction_count`,
      ),
      descriptionAffectedTransactionCount: requireNonNegativeInteger(
        preview.description_affected_transaction_count,
        `${path}.description_affected_transaction_count`,
      ),
      defaultCategoryAffectedTransactionCount: requireNonNegativeInteger(
        preview.default_category_affected_transaction_count,
        `${path}.default_category_affected_transaction_count`,
      ),
      totalAffectedTransactionCount: requireNonNegativeInteger(
        preview.total_affected_transaction_count,
        `${path}.total_affected_transaction_count`,
      ),
      preservedMerchantExceptionCount: requireNonNegativeInteger(
        preview.preserved_merchant_exception_count,
        `${path}.preserved_merchant_exception_count`,
      ),
      preservedCategoryExceptionCount: requireNonNegativeInteger(
        preview.preserved_category_exception_count,
        `${path}.preserved_category_exception_count`,
      ),
    });
  }

  function normalizeManualDescription(value) {
    if (typeof value !== "string") {
      throw new TypeError("Manual description 必须是字符串。");
    }
    return value.trim().toLocaleLowerCase().replace(/\s+/gu, "");
  }

  function findSimilarManualDescriptions(query, descriptions, limit = 5) {
    if (!Array.isArray(descriptions)) {
      throw new TypeError("Manual descriptions 必须是数组。");
    }
    if (!Number.isInteger(limit) || limit <= 0) {
      throw new TypeError("Manual description 候选数量必须是正整数。");
    }
    const normalizedQuery = normalizeManualDescription(query);
    if (normalizedQuery === "") {
      return Object.freeze([]);
    }
    const matches = [];
    descriptions.forEach((description, index) => {
      if (typeof description !== "string" || description.trim() === "") {
        return;
      }
      const normalized = normalizeManualDescription(description);
      let score = null;
      if (normalized === normalizedQuery) {
        score = 0;
      } else if (normalized.startsWith(normalizedQuery) || normalizedQuery.startsWith(normalized)) {
        score = 1;
      }
      if (score !== null) {
        matches.push({ description, score, index });
      }
    });
    matches.sort((left, right) => left.score - right.score || left.index - right.index);
    return Object.freeze(matches.slice(0, limit).map((item) => item.description));
  }

  function normalizeMerchantName(value) {
    if (typeof value !== "string") {
      throw new TypeError("Merchant 名称必须是字符串。");
    }
    return value.trim().toLocaleLowerCase().replace(/\s+/gu, "");
  }

  function findSimilarMerchantNames(query, merchants, limit = 5) {
    if (!Array.isArray(merchants)) {
      throw new TypeError("Merchant 候选必须是数组。");
    }
    if (!Number.isInteger(limit) || limit <= 0) {
      throw new TypeError("Merchant 候选数量必须是正整数。");
    }
    const normalizedQuery = normalizeMerchantName(query);
    if (normalizedQuery === "") {
      return Object.freeze([]);
    }
    const matches = [];
    merchants.forEach((merchant, index) => {
      const name = typeof merchant === "string" ? merchant : merchant && merchant.name;
      if (typeof name !== "string" || name.trim() === "") {
        return;
      }
      const normalized = normalizeMerchantName(name);
      let score = null;
      if (normalized === normalizedQuery) {
        score = 0;
      } else if (normalized.startsWith(normalizedQuery) || normalizedQuery.startsWith(normalized)) {
        score = 1;
      } else if (normalized.includes(normalizedQuery) || normalizedQuery.includes(normalized)) {
        score = 2;
      }
      if (score !== null) {
        matches.push({ name, score, index });
      }
    });
    matches.sort((left, right) => left.score - right.score || left.index - right.index);
    return Object.freeze(matches.slice(0, limit).map((item) => item.name));
  }

  function normalizeBaseUrl(value) {
    const base = value || DEFAULT_API_BASE;
    if (typeof base !== "string" || base.trim() === "") {
      throw new TypeError("Application API baseUrl 必须是非空字符串。");
    }
    return base.replace(/\/+$/, "");
  }

  function manualInputCommand(command, path) {
    const input = requireRecord(command, path);
    const allowed = new Set(["type", "date", "amount", "description", "note"]);
    const keys = Object.keys(input);
    const unknown = keys.filter((key) => !allowed.has(key));
    if (unknown.length > 0) {
      throw new TypeError(`Manual Input 包含未知字段：${unknown.join(", ")}`);
    }
    for (const required of ["type", "date", "amount", "description"]) {
      if (!Object.prototype.hasOwnProperty.call(input, required)) {
        throw new TypeError(`Manual Input 缺少必填字段：${required}`);
      }
    }
    const type = requireString(input.type, `${path}.type`);
    if (type !== "income" && type !== "expense") {
      throw new TypeError("Manual Input type 必须是 income 或 expense。");
    }
    const date = requireString(input.date, `${path}.date`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      throw new TypeError("Manual Input date 必须使用 YYYY-MM-DD 格式。");
    }
    const body = {
      type,
      date,
      amount: requireAmountString(input.amount, `${path}.amount`),
      description: requireString(input.description, `${path}.description`).trim(),
    };
    if (Object.prototype.hasOwnProperty.call(input, "note")) {
      body.note = requireNullableString(input.note, `${path}.note`);
    }
    return body;
  }

  function scheduledInputCommand(command, path) {
    const input = requireRecord(command, path);
    const allowed = new Set([
      "type",
      "amount",
      "description",
      "note",
      "nextDate",
      "enabled",
    ]);
    const unknown = Object.keys(input).filter((key) => !allowed.has(key));
    if (unknown.length > 0) {
      throw new TypeError(`Scheduled Input 包含未知字段：${unknown.join(", ")}`);
    }
    for (const required of ["type", "amount", "description", "nextDate", "enabled"]) {
      if (!Object.prototype.hasOwnProperty.call(input, required)) {
        throw new TypeError(`Scheduled Input 缺少必填字段：${required}`);
      }
    }
    const type = requireString(input.type, `${path}.type`);
    if (type !== "income" && type !== "expense") {
      throw new TypeError("Scheduled Input type 必须是 income 或 expense。");
    }
    return {
      type,
      amount: requireAmountString(input.amount, `${path}.amount`),
      description: requireString(input.description, `${path}.description`).trim(),
      note:
        input.note === undefined
          ? null
          : requireNullableString(input.note, `${path}.note`),
      next_date: requireScheduledDate(input.nextDate, `${path}.nextDate`),
      enabled: requireBoolean(input.enabled, `${path}.enabled`),
    };
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

    async function getManualDescriptions() {
      const payload = await request("/manual-descriptions");
      const descriptions = requireArray(payload.descriptions, "descriptions").map(
        (description, index) => requireString(description, `descriptions[${index}]`),
      );
      return Object.freeze(descriptions);
    }

    async function getManualInputs() {
      const payload = await request("/manual-inputs");
      const items = requireArray(payload.manual_inputs, "manual_inputs").map(
        (item, index) => validateManualInputRecord(item, `manual_inputs[${index}]`),
      );
      return Object.freeze(items);
    }

    async function getScheduledInputs() {
      const payload = await request("/scheduled-inputs");
      const items = requireArray(payload.scheduled_inputs, "scheduled_inputs").map(
        (item, index) =>
          validateScheduledInputRule(item, `scheduled_inputs[${index}]`),
      );
      return Object.freeze(items);
    }

    async function createScheduledInput(command) {
      const body = scheduledInputCommand(command, "scheduledInput");
      const payload = await request("/scheduled-inputs", { method: "POST", body });
      return validateScheduledInputRule(payload.scheduled_input);
    }

    async function updateScheduledInput(ruleId, command) {
      const id = requireString(ruleId, "ruleId");
      const body = scheduledInputCommand(command, "scheduledInputUpdate");
      const payload = await request(`/scheduled-inputs/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body,
      });
      return validateScheduledInputRule(payload.scheduled_input);
    }

    async function deleteScheduledInput(ruleId) {
      const id = requireString(ruleId, "ruleId");
      const payload = await request(`/scheduled-inputs/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      const deletion = requireRecord(
        payload.scheduled_input_deletion,
        "scheduled_input_deletion",
      );
      return Object.freeze({
        id: requireString(deletion.id, "scheduled_input_deletion.id"),
      });
    }

    async function runDueScheduledInputs() {
      const payload = await request("/scheduled-inputs/run-due", { method: "POST" });
      return validateScheduledInputRun(payload.scheduled_input_run);
    }

    async function getMappingReviews() {
      const payload = await request("/mapping-reviews");
      return validateMappingReviewWorkspace(payload.mapping_review);
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

    async function createManualInput(command) {
      const body = manualInputCommand(command, "manualInput");
      const payload = await request("/manual-inputs", { method: "POST", body });
      return validateManualInputResult(payload.manual_input);
    }

    async function correctManualInput(sourceRecordId, command) {
      const id = requireString(sourceRecordId, "sourceRecordId");
      const body = manualInputCommand(command, "manualInputCorrection");
      const payload = await request(
        `/manual-inputs/${encodeURIComponent(id)}/corrections`,
        { method: "POST", body },
      );
      return validateManualInputCorrection(payload.manual_input_correction);
    }

    async function deleteManualInput(sourceRecordId) {
      const id = requireString(sourceRecordId, "sourceRecordId");
      const payload = await request(`/manual-inputs/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      return validateManualInputDeletion(payload.manual_input_deletion);
    }

    function mappingReviewCommand(command, path) {
      const input = requireRecord(command, path);
      const allowed = new Set(["description", "merchant", "category"]);
      const keys = Object.keys(input);
      const unknown = keys.filter((key) => !allowed.has(key));
      if (unknown.length > 0) {
        throw new TypeError(`Mapping Review 包含未知字段：${unknown.join(", ")}`);
      }
      for (const required of allowed) {
        if (!Object.prototype.hasOwnProperty.call(input, required)) {
          throw new TypeError(`Mapping Review 缺少必填字段：${required}`);
        }
      }
      return {
        description: requireString(input.description, `${path}.description`).trim(),
        merchant: requireString(input.merchant, `${path}.merchant`).trim(),
        category: requireString(input.category, `${path}.category`).trim(),
      };
    }

    async function previewMappingReview(command) {
      const body = mappingReviewCommand(command, "mappingReview");
      const payload = await request("/mapping-reviews/preview", {
        method: "POST",
        body,
      });
      return validateMappingReviewPreview(payload.preview);
    }

    async function applyMappingReview(command) {
      const input = requireRecord(command, "mappingReviewApply");
      const base = mappingReviewCommand(
        {
          description: input.description,
          merchant: input.merchant,
          category: input.category,
        },
        "mappingReviewApply",
      );
      const allowed = new Set([
        "description",
        "merchant",
        "category",
        "previewToken",
        "confirmNewMerchant",
      ]);
      const unknown = Object.keys(input).filter((key) => !allowed.has(key));
      if (unknown.length > 0) {
        throw new TypeError(`Mapping Review Apply 包含未知字段：${unknown.join(", ")}`);
      }
      const body = {
        ...base,
        preview_token: requireString(
          input.previewToken,
          "mappingReviewApply.previewToken",
        ),
        confirm_new_merchant:
          input.confirmNewMerchant === undefined
            ? false
            : requireBoolean(
                input.confirmNewMerchant,
                "mappingReviewApply.confirmNewMerchant",
              ),
      };
      const payload = await request("/mapping-reviews/apply", {
        method: "POST",
        body,
      });
      return validateMappingReviewPreview(payload.mapping_review, "mapping_review");
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
      getManualDescriptions,
      getManualInputs,
      getScheduledInputs,
      createScheduledInput,
      updateScheduledInput,
      deleteScheduledInput,
      runDueScheduledInputs,
      getMappingReviews,
      getTransactions,
      getTransaction,
      createManualInput,
      correctManualInput,
      deleteManualInput,
      previewMappingReview,
      applyMappingReview,
      updateEnrichment,
    });
  }

  return Object.freeze({
    ApplicationApiError,
    DEFAULT_API_BASE,
    createApplicationService,
    normalizeManualDescription,
    findSimilarManualDescriptions,
    normalizeMerchantName,
    findSimilarMerchantNames,
    validateTransaction,
    validateManualInputResult,
    validateManualInputRecord,
    validateManualInputCorrection,
    validateManualInputDeletion,
    validateScheduledInputRule,
    validateScheduledInputRun,
    validateMappingReviewItem,
    validateMappingReviewWorkspace,
    validateMappingReviewPreview,
  });
});
