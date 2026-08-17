export type RequestMethod = "GET" | "POST" | "PATCH" | "DELETE";
export type TransactionType = "income" | "expense";
export type ManualInputAction = "created" | "matched" | "reused";
export type FeedbackStatus = "open" | "resolved";
export type FeedbackRuntime = "desktop_web" | "mini_h5" | "weapp";
export type ScheduledInputAction = "created" | "matched" | "reused" | "recovered";

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

export interface MappingReviewPreview {
  token: string;
  description: string;
  merchant: string;
  category: string;
  is_new_merchant: boolean;
  previous_default_category: string | null;
  description_transaction_count: number;
  description_affected_transaction_count: number;
  default_category_affected_transaction_count: number;
  total_affected_transaction_count: number;
  preserved_merchant_exception_count: number;
  preserved_category_exception_count: number;
}

export interface PreviewMappingReviewCommand {
  description: string;
  merchant: string;
  category: string;
}

export interface ApplyMappingReviewCommand extends PreviewMappingReviewCommand {
  preview_token: string;
  confirm_new_merchant: boolean;
}

export interface CreateManualInputCommand {
  type: TransactionType;
  date: string;
  amount: string;
  description: string;
  note: string | null;
}

export interface ManualInputResult {
  source_record_id: string;
  action: ManualInputAction;
  transaction: Transaction;
}

export interface FeedbackContext {
  runtime?: FeedbackRuntime;
  page?: string;
  workspace?: string;
  entity_type?: string;
  entity_id?: string;
}

export interface FeedbackItem {
  id: string;
  created_at: string;
  status: FeedbackStatus;
  content: string;
  context: FeedbackContext;
}

export interface CreateFeedbackCommand {
  content: string;
  context?: FeedbackContext;
}

export interface ScheduledInputRule {
  id: string;
  enabled: boolean;
  type: TransactionType;
  amount: string;
  currency: string;
  description: string;
  note: string | null;
  next_date: string;
  last_occurrence_date: string | null;
  last_source_record_id: string | null;
  last_transaction_id: string | null;
  last_action: ScheduledInputAction | null;
}

export interface ScheduledInputCommand {
  type: TransactionType;
  amount: string;
  description: string;
  next_date: string;
  note: string | null;
  enabled: boolean;
}

export interface ScheduledInputOccurrence {
  rule_id: string;
  occurrence_date: string;
  source_record_id: string;
  transaction_id: string;
  action: ScheduledInputAction;
}

export interface ScheduledInputRun {
  generated_count: number;
  occurrences: ScheduledInputOccurrence[];
}

export interface FamilySpendingApi {
  health(): Promise<void>;
  financialSummary(): Promise<FinancialSummary>;
  transactions(): Promise<Transaction[]>;
  transaction(transactionId: string): Promise<Transaction>;
  mappingReview(): Promise<MappingReviewWorkspace>;
  previewMappingReview(command: PreviewMappingReviewCommand): Promise<MappingReviewPreview>;
  applyMappingReview(command: ApplyMappingReviewCommand): Promise<MappingReviewPreview>;
  manualDescriptions(): Promise<string[]>;
  createManualInput(command: CreateManualInputCommand): Promise<ManualInputResult>;
  feedback(): Promise<FeedbackItem[]>;
  createFeedback(command: CreateFeedbackCommand): Promise<FeedbackItem>;
  updateFeedbackStatus(feedbackId: string, status: FeedbackStatus): Promise<FeedbackItem>;
  scheduledInputs(): Promise<ScheduledInputRule[]>;
  createScheduledInput(command: ScheduledInputCommand): Promise<ScheduledInputRule>;
  updateScheduledInput(ruleId: string, command: ScheduledInputCommand): Promise<ScheduledInputRule>;
  deleteScheduledInput(ruleId: string): Promise<string>;
  runDueScheduledInputs(): Promise<ScheduledInputRun>;
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

function requireNonNegativeInteger(value: unknown, label: string): number {
  const number = requireNumber(value, label);
  if (!Number.isSafeInteger(number) || number < 0) {
    throw new Error(`${label} 必须是非负整数`);
  }
  return number;
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label} 必须是非空字符串`);
  }
  return value;
}

function requireNullableString(value: unknown, label: string): string | null {
  if (value === null) {
    return null;
  }
  return requireString(value, label);
}

function requireBoolean(value: unknown, label: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${label} 必须是布尔值`);
  }
  return value;
}

function requireStringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} 必须是字符串数组`);
  }
  return value.map((item, index) => requireString(item, `${label}[${index}]`));
}

function requireSha256(value: unknown, label: string): string {
  const token = requireString(value, label);
  if (!/^[0-9a-f]{64}$/.test(token)) {
    throw new Error(`${label} 必须是 SHA-256 token`);
  }
  return token;
}

function requireTransactionType(value: unknown, label: string): TransactionType {
  const type = requireString(value, label);
  if (type !== "income" && type !== "expense") {
    throw new Error(`${label} 必须是 income 或 expense`);
  }
  return type;
}

function requireFeedbackStatus(value: unknown, label: string): FeedbackStatus {
  const status = requireString(value, label);
  if (status !== "open" && status !== "resolved") {
    throw new Error(`${label} 必须是 open 或 resolved`);
  }
  return status;
}

function requireScheduledAction(value: unknown, label: string): ScheduledInputAction {
  const action = requireString(value, label);
  if (action !== "created" && action !== "matched" && action !== "reused" && action !== "recovered") {
    throw new Error(`${label} 是未知 Scheduled Input action`);
  }
  return action;
}

function requireScheduledDate(value: unknown, label: string): string {
  const date = requireString(value, label);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date);
  if (!match) {
    throw new Error(`${label} 必须使用 YYYY-MM-DD`);
  }
  const day = Number(match[3]);
  if (day < 1 || day > 28) {
    throw new Error(`${label} 日期必须在每月 1–28 日`);
  }
  return date;
}

function parseTotals(value: unknown, label: string): FinancialSummaryTotals {
  if (!isRecord(value)) {
    throw new Error(`${label} 格式错误`);
  }
  return {
    total_income_minor: requireNumber(value.total_income_minor, `${label}.total_income_minor`),
    total_spending_minor: requireNumber(value.total_spending_minor, `${label}.total_spending_minor`),
    net_cash_flow_minor: requireNumber(value.net_cash_flow_minor, `${label}.net_cash_flow_minor`),
    income_transaction_count: requireNumber(value.income_transaction_count, `${label}.income_transaction_count`),
    spending_transaction_count: requireNumber(value.spending_transaction_count, `${label}.spending_transaction_count`),
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
    spending_data_complete: requireBoolean(value.spending_data_complete, `${label}.spending_data_complete`),
    show: requireBoolean(value.show, `${label}.show`),
    total_income_minor: requireNumber(value.total_income_minor, `${label}.total_income_minor`),
    income_transaction_count: requireNumber(value.income_transaction_count, `${label}.income_transaction_count`),
    total_spending_minor: requireNumber(value.total_spending_minor, `${label}.total_spending_minor`),
    spending_transaction_count: requireNumber(value.spending_transaction_count, `${label}.spending_transaction_count`),
    net_cash_flow_minor: requireNumber(value.net_cash_flow_minor, `${label}.net_cash_flow_minor`),
  };
}

function parseFinancialSummary(value: unknown): FinancialSummary {
  if (!isRecord(value) || !isRecord(value.summary) || !Array.isArray(value.months)) {
    throw new Error("financial_summary 格式错误");
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

function parseTransaction(value: unknown, label: string): Transaction {
  if (!isRecord(value) || !isRecord(value.source) || !isRecord(value.enrichment)) {
    throw new Error(`${label} 格式错误`);
  }
  return {
    id: requireString(value.id, `${label}.id`),
    type: requireTransactionType(value.type, `${label}.type`),
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
      display_name: requireNullableString(value.enrichment.display_name, `${label}.enrichment.display_name`),
      default_category: requireNullableString(value.enrichment.default_category, `${label}.enrichment.default_category`),
      category: requireNullableString(value.enrichment.category, `${label}.enrichment.category`),
      category_source: requireNullableString(value.enrichment.category_source, `${label}.enrichment.category_source`),
      note: requireNullableString(value.enrichment.note, `${label}.enrichment.note`),
      is_unclassified: requireBoolean(value.enrichment.is_unclassified, `${label}.enrichment.is_unclassified`),
      review_signals: requireStringArray(value.enrichment.review_signals, `${label}.enrichment.review_signals`),
    },
  };
}

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
        transaction_only_exception_count: requireNumber(item.transaction_only_exception_count, `${label}.transaction_only_exception_count`),
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

function parseMappingReviewPreview(value: unknown, label: string): MappingReviewPreview {
  if (!isRecord(value)) {
    throw new Error(`${label} 格式错误`);
  }
  return {
    token: requireSha256(value.token, `${label}.token`),
    description: requireString(value.description, `${label}.description`),
    merchant: requireString(value.merchant, `${label}.merchant`),
    category: requireString(value.category, `${label}.category`),
    is_new_merchant: requireBoolean(value.is_new_merchant, `${label}.is_new_merchant`),
    previous_default_category: requireNullableString(value.previous_default_category, `${label}.previous_default_category`),
    description_transaction_count: requireNumber(value.description_transaction_count, `${label}.description_transaction_count`),
    description_affected_transaction_count: requireNumber(value.description_affected_transaction_count, `${label}.description_affected_transaction_count`),
    default_category_affected_transaction_count: requireNumber(value.default_category_affected_transaction_count, `${label}.default_category_affected_transaction_count`),
    total_affected_transaction_count: requireNumber(value.total_affected_transaction_count, `${label}.total_affected_transaction_count`),
    preserved_merchant_exception_count: requireNumber(value.preserved_merchant_exception_count, `${label}.preserved_merchant_exception_count`),
    preserved_category_exception_count: requireNumber(value.preserved_category_exception_count, `${label}.preserved_category_exception_count`),
  };
}

function parseManualInputResult(value: unknown): ManualInputResult {
  if (!isRecord(value)) {
    throw new Error("manual_input 格式错误");
  }
  const action = requireString(value.action, "manual_input.action");
  if (action !== "created" && action !== "matched" && action !== "reused") {
    throw new Error("manual_input.action 格式错误");
  }
  return {
    source_record_id: requireString(value.source_record_id, "manual_input.source_record_id"),
    action,
    transaction: parseTransaction(value.transaction, "manual_input.transaction"),
  };
}

function parseFeedbackContext(value: unknown, label: string): FeedbackContext {
  if (!isRecord(value)) {
    throw new Error(`${label} 格式错误`);
  }
  const context: FeedbackContext = {};
  if ("runtime" in value) {
    const runtime = requireString(value.runtime, `${label}.runtime`);
    if (runtime !== "desktop_web" && runtime !== "mini_h5" && runtime !== "weapp") {
      throw new Error(`${label}.runtime 格式错误`);
    }
    context.runtime = runtime;
  }
  if ("page" in value) context.page = requireString(value.page, `${label}.page`);
  if ("workspace" in value) context.workspace = requireString(value.workspace, `${label}.workspace`);
  if ("entity_type" in value) context.entity_type = requireString(value.entity_type, `${label}.entity_type`);
  if ("entity_id" in value) context.entity_id = requireString(value.entity_id, `${label}.entity_id`);
  if ((context.entity_type === undefined) !== (context.entity_id === undefined)) {
    throw new Error(`${label} entity_type/entity_id 必须同时存在`);
  }
  return context;
}

function parseFeedback(value: unknown, label: string): FeedbackItem {
  if (!isRecord(value)) {
    throw new Error(`${label} 格式错误`);
  }
  const createdAt = requireString(value.created_at, `${label}.created_at`);
  if (!createdAt.endsWith("Z") || Number.isNaN(Date.parse(createdAt))) {
    throw new Error(`${label}.created_at 必须是 UTC 时间`);
  }
  return {
    id: requireString(value.id, `${label}.id`),
    created_at: createdAt,
    status: requireFeedbackStatus(value.status, `${label}.status`),
    content: requireString(value.content, `${label}.content`),
    context: parseFeedbackContext(value.context, `${label}.context`),
  };
}

function parseScheduledRule(value: unknown, label: string): ScheduledInputRule {
  if (!isRecord(value)) {
    throw new Error(`${label} 格式错误`);
  }
  const lastOccurrence = value.last_occurrence_date === null ? null : requireScheduledDate(value.last_occurrence_date, `${label}.last_occurrence_date`);
  const lastSource = requireNullableString(value.last_source_record_id, `${label}.last_source_record_id`);
  const lastTransaction = requireNullableString(value.last_transaction_id, `${label}.last_transaction_id`);
  const lastAction = value.last_action === null ? null : requireScheduledAction(value.last_action, `${label}.last_action`);
  const present = [lastOccurrence, lastSource, lastTransaction, lastAction].filter((item) => item !== null).length;
  if (present !== 0 && present !== 4) {
    throw new Error(`${label} 最近执行元数据不完整`);
  }
  return {
    id: requireString(value.id, `${label}.id`),
    enabled: requireBoolean(value.enabled, `${label}.enabled`),
    type: requireTransactionType(value.type, `${label}.type`),
    amount: requireString(value.amount, `${label}.amount`),
    currency: requireString(value.currency, `${label}.currency`),
    description: requireString(value.description, `${label}.description`),
    note: requireNullableString(value.note, `${label}.note`),
    next_date: requireScheduledDate(value.next_date, `${label}.next_date`),
    last_occurrence_date: lastOccurrence,
    last_source_record_id: lastSource,
    last_transaction_id: lastTransaction,
    last_action: lastAction,
  };
}

function parseScheduledRun(value: unknown): ScheduledInputRun {
  if (!isRecord(value) || !Array.isArray(value.occurrences)) {
    throw new Error("scheduled_input_run 格式错误");
  }
  const occurrences = value.occurrences.map((item, index) => {
    const label = `scheduled_input_run.occurrences[${index}]`;
    if (!isRecord(item)) {
      throw new Error(`${label} 格式错误`);
    }
    return {
      rule_id: requireString(item.rule_id, `${label}.rule_id`),
      occurrence_date: requireScheduledDate(item.occurrence_date, `${label}.occurrence_date`),
      source_record_id: requireString(item.source_record_id, `${label}.source_record_id`),
      transaction_id: requireString(item.transaction_id, `${label}.transaction_id`),
      action: requireScheduledAction(item.action, `${label}.action`),
    };
  });
  const generatedCount = requireNonNegativeInteger(value.generated_count, "scheduled_input_run.generated_count");
  if (generatedCount !== occurrences.length) {
    throw new Error("scheduled_input_run.generated_count 与 occurrences 数量不一致");
  }
  return { generated_count: generatedCount, occurrences };
}

function backendErrorDetail(value: unknown): string | null {
  if (!isRecord(value) || typeof value.error !== "string") {
    return null;
  }
  const message = value.error.trim();
  return message || null;
}

function requestJson(
  requester: Requester,
  baseUrl: string,
  method: RequestMethod,
  path: string,
  data?: unknown,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const options: RequestOptions = {
      url: `${baseUrl}${path}`,
      method,
      header: { "content-type": "application/json" },
      success(response) {
        if (response.statusCode < 200 || response.statusCode >= 300) {
          const detail = backendErrorDetail(response.data);
          reject(new Error(`Backend 请求失败：HTTP ${response.statusCode}${detail ? ` · ${detail}` : ""}`));
          return;
        }
        resolve(response.data);
      },
      fail(error) {
        reject(new Error(`无法连接 Backend：${error.errMsg}`));
      },
    };
    if (data !== undefined) {
      options.data = data;
    }
    requester(options);
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
      if (!isRecord(payload) || payload.status !== "ok") throw new Error("Backend health 响应格式错误");
    },
    async financialSummary() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/financial-summary");
      if (!isRecord(payload) || !("financial_summary" in payload)) throw new Error("Backend financial summary 响应格式错误");
      return parseFinancialSummary(payload.financial_summary);
    },
    async transactions() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/transactions");
      if (!isRecord(payload) || !Array.isArray(payload.transactions)) throw new Error("Backend transactions 响应格式错误");
      return payload.transactions.map((item, index) => parseTransaction(item, `transactions[${index}]`));
    },
    async transaction(transactionId) {
      if (!transactionId.trim()) throw new Error("transaction id 不能为空");
      const payload = await requestJson(requester, baseUrl, "GET", `/api/transactions/${encodeURIComponent(transactionId)}`);
      if (!isRecord(payload) || !("transaction" in payload)) throw new Error("Backend transaction detail 响应格式错误");
      return parseTransaction(payload.transaction, "transaction");
    },
    async mappingReview() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/mapping-reviews");
      if (!isRecord(payload) || !("mapping_review" in payload)) throw new Error("Backend mapping review 响应格式错误");
      return parseMappingReview(payload.mapping_review);
    },
    async previewMappingReview(command) {
      const payload = await requestJson(requester, baseUrl, "POST", "/api/mapping-reviews/preview", command);
      if (!isRecord(payload) || !("preview" in payload)) throw new Error("Backend mapping review preview 响应格式错误");
      return parseMappingReviewPreview(payload.preview, "mapping_review_preview");
    },
    async applyMappingReview(command) {
      const payload = await requestJson(requester, baseUrl, "POST", "/api/mapping-reviews/apply", command);
      if (!isRecord(payload) || !("mapping_review" in payload)) throw new Error("Backend mapping review apply 响应格式错误");
      return parseMappingReviewPreview(payload.mapping_review, "mapping_review_apply");
    },
    async manualDescriptions() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/manual-descriptions");
      if (!isRecord(payload) || !Array.isArray(payload.descriptions)) throw new Error("Backend manual descriptions 响应格式错误");
      return requireStringArray(payload.descriptions, "manual_descriptions");
    },
    async createManualInput(command) {
      const payload = await requestJson(requester, baseUrl, "POST", "/api/manual-inputs", command);
      if (!isRecord(payload) || !("manual_input" in payload)) throw new Error("Backend manual input 响应格式错误");
      return parseManualInputResult(payload.manual_input);
    },
    async feedback() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/feedback");
      if (!isRecord(payload) || !Array.isArray(payload.feedback)) throw new Error("Backend feedback 响应格式错误");
      return payload.feedback.map((item, index) => parseFeedback(item, `feedback[${index}]`));
    },
    async createFeedback(command) {
      const payload = await requestJson(requester, baseUrl, "POST", "/api/feedback", command);
      if (!isRecord(payload) || !("feedback" in payload)) throw new Error("Backend feedback create 响应格式错误");
      return parseFeedback(payload.feedback, "feedback");
    },
    async updateFeedbackStatus(feedbackId, status) {
      if (!feedbackId.trim()) throw new Error("feedback id 不能为空");
      const payload = await requestJson(requester, baseUrl, "PATCH", `/api/feedback/${encodeURIComponent(feedbackId)}`, { status });
      if (!isRecord(payload) || !("feedback" in payload)) throw new Error("Backend feedback update 响应格式错误");
      return parseFeedback(payload.feedback, "feedback");
    },
    async scheduledInputs() {
      const payload = await requestJson(requester, baseUrl, "GET", "/api/scheduled-inputs");
      if (!isRecord(payload) || !Array.isArray(payload.scheduled_inputs)) throw new Error("Backend scheduled inputs 响应格式错误");
      return payload.scheduled_inputs.map((item, index) => parseScheduledRule(item, `scheduled_inputs[${index}]`));
    },
    async createScheduledInput(command) {
      const payload = await requestJson(requester, baseUrl, "POST", "/api/scheduled-inputs", command);
      if (!isRecord(payload) || !("scheduled_input" in payload)) throw new Error("Backend scheduled input create 响应格式错误");
      return parseScheduledRule(payload.scheduled_input, "scheduled_input");
    },
    async updateScheduledInput(ruleId, command) {
      if (!ruleId.trim()) throw new Error("scheduled rule id 不能为空");
      const payload = await requestJson(requester, baseUrl, "PATCH", `/api/scheduled-inputs/${encodeURIComponent(ruleId)}`, command);
      if (!isRecord(payload) || !("scheduled_input" in payload)) throw new Error("Backend scheduled input update 响应格式错误");
      return parseScheduledRule(payload.scheduled_input, "scheduled_input");
    },
    async deleteScheduledInput(ruleId) {
      if (!ruleId.trim()) throw new Error("scheduled rule id 不能为空");
      const payload = await requestJson(requester, baseUrl, "DELETE", `/api/scheduled-inputs/${encodeURIComponent(ruleId)}`);
      if (!isRecord(payload) || !isRecord(payload.scheduled_input_deletion)) throw new Error("Backend scheduled input deletion 响应格式错误");
      return requireString(payload.scheduled_input_deletion.id, "scheduled_input_deletion.id");
    },
    async runDueScheduledInputs() {
      const payload = await requestJson(requester, baseUrl, "POST", "/api/scheduled-inputs/run-due");
      if (!isRecord(payload) || !("scheduled_input_run" in payload)) throw new Error("Backend scheduled input run 响应格式错误");
      return parseScheduledRun(payload.scheduled_input_run);
    },
  };
}