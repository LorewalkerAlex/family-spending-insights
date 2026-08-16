export type RequestMethod = "GET" | "POST" | "PATCH" | "DELETE";

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

export interface FamilySpendingApi {
  health(): Promise<void>;
  financialSummary(): Promise<FinancialSummary>;
}

export interface ApiClientOptions {
  baseUrl: string;
  requester?: Requester;
}

function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/$/, "");
}

function defaultRequester(options: RequestOptions): unknown {
  return wx.request(options);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} 必须是有限数字`);
  }
  return value;
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} 必须是非空字符串`);
  }
  return value;
}

function requireBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${label} 必须是布尔值`);
  }
  return value;
}

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
  };
}
