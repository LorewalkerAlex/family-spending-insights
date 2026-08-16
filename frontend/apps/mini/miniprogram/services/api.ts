export type RequestMethod = "GET" | "POST" | "PATCH" | "DELETE";
export type TransactionType = "income" | "expense";

export interface RequestSuccess {
  statusCode: number;
  data: unknown;
}

export interface RequestFailure {
  errMsg: string;
}

export interface RequestOptions {
  url: string;
  method: RequestMethod;
  header: Record<string, string>;
  data?: unknown;
  success: (response: RequestSuccess) => void;
  fail: (error: RequestFailure) => void;
}

export type Requester = (options: RequestOptions) => unknown;

export interface FinancialSummaryTotals {
  total_income_minor: number;
  total_spending_minor: number;
  net_cash_flow_minor: number;
  income_transaction_count: number;
  spending_transaction_count: number;
  month_count: number;
}

export interface FinancialSummaryMonth {
  month: string;
  spending_data_complete: boolean;
  show: boolean;
  total_income_minor: number;
  income_transaction_count: number;
  total_spending_minor: number;
  spending_transaction_count: number;
  net_cash_flow_minor: number;
}

export interface FinancialSummary {
  schema_version: number;
  summary: {
    all_data: FinancialSummaryTotals;
    shown_data: FinancialSummaryTotals;
  };
  months: FinancialSummaryMonth[];
}

export interface Transaction {
  id: string;
  type: TransactionType;
  date: string;
  amount: string;
  currency: string;
  source: {
    id: string;
    type: string;
    description: string | null;
  };
  enrichment: {
    merchant: string | null;
    display_name: string | null;
    default_category: string | null;
    category: string | null;
    category_source: string | null;
    note: string | null;
    is_unclassified: boolean;
    review_signals: string[];
  };
}

export interface MappingReviewItem {
  description: string;
  transaction_count: number;
  total_amount: string;
  currency: string;
  latest_date: string;
  source_types: string[];
  transaction_only_exception_count: number;
}

export interface MappingReviewWorkspace {
  items: MappingReviewItem[];
  merchants: Array<{
    name: string;
    default_category: string;
  }>;
  categories: string[];
}

export interface FamilySpendingApi {
  health(): Promise<void>;
  financialSummary(): Promise<FinancialSummary>;
  transactions(): Promise<Transaction[]>;
  mappingReview(): Promise<MappingReviewWorkspace>;
}

export interface ApiClientOptions {
  baseUrl: string;
  requester?: Requester;
}

/** Normalize configured origins once so every request path is joined consistently. */
function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/$/, "");
}

/** Delegate to the native Mini request API in production while keeping tests injectable. */
function defaultRequester(options: RequestOptions): unknown {
  return wx.request(options);
}

/** Narrow unknown JSON values before reading object properties. */
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Require finite numeric API fields so malformed transport data fails explicitly. */
function requireNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} 必须是有限数字`);
  }
  return value;
}

/** Require non-empty text for stable identifiers and canonical date/amount strings. */
function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} 必须是非空字符串`);
  }
  return value;
}

/** Preserve nullable enrichment/source fields without turning absence into fabricated text. */
function requireNullableString(value: unknown, label: string): string | null {
  if (value === null) {
    return null;
  }
  return requireString(value, label);
}

/** Require canonical booleans instead of accepting truthy transport values. */
function requireBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${label} 必须是布尔值`);
  }
  return value;
}

/** Parse a strict string array used by review signals, source types, and category options. */
function requireStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} 必须是字符串数组`);
  }
  return value.map((item, index) => requireString(item, `${label}[${index}]`));
}

/** Parse one summary totals object shared by all-data and shown-data sections. */
function parseTotals(value: unknown, label: string): FinancialSummaryTotals {
  if (!isRecord(value)) {
    throw new Error(`${label} 格式错误`);
  }
  return {
    total_income_minor: requireNumber(value.total_income_minor, `${label}.total_income_minor`),
    total_spending_minor: requireNumber(value.total_spending_minor, `${label}.total_spending_minor`),
    net_cash_flow_minor: requireNumber(value.net_cash_flow_minor, `${label}.net_cash_flow_minor`),
    income_transaction_count: requireNumber(
      value.income_transaction_count,
      `${label}.income_transaction_count`,
    ),
    spending_transaction_count: requireNumber(
      value.spending_transaction_count,
      `${label}.spending_transaction_count`,
    ),
    month_count: requireNumber(value.month_count, `${label}.month_count`),
  };
}

/** Parse one monthly summary row without changing Backend visibility/completeness semantics. */
function parseMonth(value: unknown, index: number): FinancialSummaryMonth {
  const label = `financial_summary.months[${index}]`;
  if (!isRecord(value)) {
    throw new Error(`${label} 格式错误`);
  }
  return {
    month: requireString(value.month, `${label}.month`),
    spending_data_complete: requireBoolean(
      value.spending_data_complete,
      `${label}.spending_data_complete`,
    ),
    show: requireBoolean(value.show, `${label}.show`),
    total_income_minor: requireNumber(value.total_income_minor, `${label}.total_income_minor`),
    income_transaction_count: requireNumber(
      value.income_transaction_count,
      `${label}.income_transaction_count`,
    ),
    total_spending_minor: requireNumber(
      value.total_spending_minor,
      `${label}.total_spending_minor`,
    ),
    spending_transaction_count: requireNumber(
      value.spending_transaction_count,
      `${label}.spending_transaction_count`,
    ),
    net_cash_flow_minor: requireNumber(
      value.net_cash_flow_minor,
      `${label}.net_cash_flow_minor`,
    ),
  };
}

/** Parse the Financial Summary response owned by the Canonical Application. */
function parseFinancialSummary(value: unknown): FinancialSummary {
  if (!isRecord(value)) {
    throw new Error("financial_summary 格式错误");
  }
  if (!isRecord(value.summary)) {
    throw new Error("financial_summary.summary 格式错误");
  }
  if (!Array.isArray(value.months)) {
    throw new Error("financial_summary.months 格式错误");
  }
  return {
    schema_version: requireNumber(value.schema_version, "financial_summary.schema_version"),
    summary: {
      all_data: parseTotals(value.summary.all_data, "financial_summary.summary.all_data"),
      shown_data: parseTotals(value.summary.shown_data, "financial_summary.summary.shown_data"),
    },
    months: value.months.map(parseMonth),
  };
}

/** Parse one stable frontend Transaction DTO without adding client-side domain rules. */
function parseTransaction(value: unknown, index: number): Transaction {
  const label = `transactions[${index}]`;
  if (!isRecord(value) || !isRecord(value.source) || !isRecord(value.enrichment)) {
    throw new Error(`${label} 格式错误`);
  }
  const type = requireString(value.type, `${label}.type`);
  if (type !== "income" && type !== "expense") {
    throw new Error(`${label}.type 必须是 income 或 expense`);
  }
  return {
    id: requireString(value.id, `${label}.id`),
    type,
    date: requireString(value.date, `${label}.date`),
    amount: requireString(value.amount, `${label}.amount`),
    currency: requireString(value.currency, `${label}.currency`),
    source: {
      id: requireString(value.source.id, `${label}.source.id`),
      type: requireString(value.source.type, `${label}.source.type`),
      description: requireNullableString(value.source.description, `${label}.source.description`),
    },
    enrichment: {
      merchant: requireNullableString(value.enrichment.merchant, `${label}.enrichment.merchant`),
      display_name: requireNullableString(
        value.enrichment.display_name,
        `${label}.enrichment.display_name`,
      ),
      default_category: requireNullableString(
        value.enrichment.default_category,
        `${label}.enrichment.default_category`,
      ),
      category: requireNullableString(value.enrichment.category, `${label}.enrichment.category`),
      category_source: requireNullableString(
        value.enrichment.category_source,
        `${label}.enrichment.category_source`,
      ),
      note: requireNullableString(value.enrichment.note, `${label}.enrichment.note`),
      is_unclassified: requireBoolean(
        value.enrichment.is_unclassified,
        `${label}.enrichment.is_unclassified`,
      ),
      review_signals: requireStringArray(
        value.enrichment.review_signals,
        `${label}.enrichment.review_signals`,
      ),
    },
  };
}

/** Parse the Mapping Review workspace used by Home now and the Review page later. */
function parseMappingReview(value: unknown): MappingReviewWorkspace {
  if (!isRecord(value) || !Array.isArray(value.items) || !Array.isArray(value.merchants)) {
    throw new Error("mapping_review 格式错误");
  }

  return {
    items: value.items.map((item, index) => {
      const label = `mapping_review.items[${index}]`;
      if (!isRecord(item)) {
        throw new Error(`${label} 格式错误`);
      }
      return {
        description: requireString(item.description, `${label}.description`),
        transaction_count: requireNumber(item.transaction_count, `${label}.transaction_count`),
        total_amount: requireString(item.total_amount, `${label}.total_amount`),
        currency: requireString(item.currency, `${label}.currency`),
        latest_date: requireString(item.latest_date, `${label}.latest_date`),
        source_types: requireStringArray(item.source_types, `${label}.source_types`),
        transaction_only_exception_count: requireNumber(
          item.transaction_only_exception_count,
          `${label}.transaction_only_exception_count`,
        ),
      };
    }),
    merchants: value.merchants.map((merchant, index) => {
      const label = `mapping_review.merchants[${index}]`;
      if (!isRecord(merchant)) {
        throw new Error(`${label} 格式错误`);
      }
      return {
        name: requireString(merchant.name, `${label}.name`),
        default_category: requireString(merchant.default_category, `${label}.default_category`),
      };
    }),
    categories: requireStringArray(value.categories, "mapping_review.categories"),
  };
}

/** Execute one JSON request and keep HTTP/network failures explicit for page error states. */
function requestJson(
  requester: Requester,
  baseUrl: string,
  method: RequestMethod,
  path: string,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    requester({
      url: `${baseUrl}${path}`,
      method,
      header: {
        "content-type": "application/json",
      },
      success(response) {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`Backend 请求失败：HTTP ${response.statusCode}`));
          return;
        }
        resolve(response.data);
      },
      fail(error) {
        reject(new Error(`无法连接 Backend：${error.errMsg}`));
      },
    });
  });
}

/** Create the native Mini transport adapter over the existing Canonical HTTP contract. */
export function createFamilySpendingApi(options: ApiClientOptions): FamilySpendingApi {
  const baseUrl = normalizeBaseUrl(options.baseUrl);
  if (!/^https?:\/\//.test(baseUrl)) {
    throw new Error("API 地址必须是绝对 HTTP(S) 地址");
  }
  const requester = options.requester ?? defaultRequester;

  return {
    async health() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/health");
      if (!isRecord(payload) || payload.status !== "ok") {
        throw new Error("Backend health 响应格式错误");
      }
    },

    async financialSummary() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/financial-summary");
      if (!isRecord(payload) || !("financial_summary" in payload)) {
        throw new Error("Backend financial summary 响应格式错误");
      }
      return parseFinancialSummary(payload.financial_summary);
    },

    async transactions() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/transactions");
      if (!isRecord(payload) || !Array.isArray(payload.transactions)) {
        throw new Error("Backend transactions 响应格式错误");
      }
      return payload.transactions.map(parseTransaction);
    },

    async mappingReview() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/mapping-reviews");
      if (!isRecord(payload) || !("mapping_review" in payload)) {
        throw new Error("Backend mapping review 响应格式错误");
      }
      return parseMappingReview(payload.mapping_review);
    },
  };
}
