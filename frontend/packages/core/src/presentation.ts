import type {
  FeedbackItem,
  FinancialSummary,
  ManualInputAction,
  ManualInputRecord,
  MappingReviewItem,
  MappingReviewPreview,
  MerchantMappingOption,
  ScheduledInputAction,
  ScheduledInputRule,
  Transaction,
} from "./contracts";

export function formatCnyMinorUnits(value: number): string {
  if (!Number.isSafeInteger(value)) {
    throw new TypeError("CNY minor units must be a safe integer");
  }
  const sign = value < 0 ? "-" : "";
  const absolute = Math.abs(value);
  const yuan = Math.floor(absolute / 100)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const cents = String(absolute % 100).padStart(2, "0");
  return `${sign}¥${yuan}.${cents}`;
}

export function formatDecimalCurrency(amount: string, currency: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
  if (!match) {
    throw new TypeError("Amount must be a decimal string");
  }
  const sign = match[1] ?? "";
  const integer = match[2];
  if (integer === undefined) {
    throw new TypeError("Amount must include an integer component");
  }
  const rawFraction = match[3] ?? "";
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = rawFraction.padEnd(2, "0");
  const value = `${sign}${grouped}.${fraction}`;
  return currency === "CNY" ? `${sign}¥${grouped}.${fraction}` : `${currency} ${value}`;
}

export interface FinancialHeroViewModel {
  netCashFlowMinor: number;
  netCashFlowText: string;
  totalIncomeMinor: number;
  totalIncomeText: string;
  totalSpendingMinor: number;
  totalSpendingText: string;
  monthCount: number;
}

export interface FinancialMonthViewModel {
  month: string;
  spendingDataComplete: boolean;
  totalIncomeMinor: number;
  totalIncomeText: string;
  totalSpendingMinor: number;
  totalSpendingText: string;
  netCashFlowMinor: number;
  netCashFlowText: string;
}

export interface FinancialTrendPointViewModel {
  month: string;
  totalIncomeText: string;
  totalSpendingText: string;
  netCashFlowMinor: number;
  netCashFlowText: string;
  incomeHeightPercent: number;
  spendingHeightPercent: number;
}

export interface FinancialTrendViewModel {
  maxAmountText: string;
  points: readonly FinancialTrendPointViewModel[];
}

export interface FinancialSummaryViewModel {
  hero: FinancialHeroViewModel;
  visibleMonths: readonly FinancialMonthViewModel[];
}

export function toFinancialSummaryViewModel(summary: FinancialSummary): FinancialSummaryViewModel {
  const shown = summary.summary.shown_data;
  return {
    hero: {
      netCashFlowMinor: shown.net_cash_flow_minor,
      netCashFlowText: formatCnyMinorUnits(shown.net_cash_flow_minor),
      totalIncomeMinor: shown.total_income_minor,
      totalIncomeText: formatCnyMinorUnits(shown.total_income_minor),
      totalSpendingMinor: shown.total_spending_minor,
      totalSpendingText: formatCnyMinorUnits(shown.total_spending_minor),
      monthCount: shown.month_count,
    },
    visibleMonths: summary.months
      .filter((month) => month.show)
      .map((month) => ({
        month: month.month,
        spendingDataComplete: month.spending_data_complete,
        totalIncomeMinor: month.total_income_minor,
        totalIncomeText: formatCnyMinorUnits(month.total_income_minor),
        totalSpendingMinor: month.total_spending_minor,
        totalSpendingText: formatCnyMinorUnits(month.total_spending_minor),
        netCashFlowMinor: month.net_cash_flow_minor,
        netCashFlowText: formatCnyMinorUnits(month.net_cash_flow_minor),
      })),
  };
}

/** Build a chart-ready view from backend-authoritative monthly facts without recalculating finance semantics. */
export function toFinancialTrendViewModel(
  summary: FinancialSummary,
  maxMonths = 12,
): FinancialTrendViewModel {
  if (!Number.isInteger(maxMonths) || maxMonths <= 0) {
    throw new TypeError("Financial trend month limit must be a positive integer");
  }

  const months = summary.months
    .filter((month) => month.show)
    .slice()
    .sort((left, right) => left.month.localeCompare(right.month))
    .slice(-maxMonths);

  const maxAmount = Math.max(
    1,
    ...months.flatMap((month) => [month.total_income_minor, month.total_spending_minor]),
  );
  const heightPercent = (value: number): number => {
    if (value <= 0) return 0;
    return Math.max(2, Math.round((value / maxAmount) * 1000) / 10);
  };

  return {
    maxAmountText: formatCnyMinorUnits(maxAmount),
    points: months.map((month) => ({
      month: month.month,
      totalIncomeText: formatCnyMinorUnits(month.total_income_minor),
      totalSpendingText: formatCnyMinorUnits(month.total_spending_minor),
      netCashFlowMinor: month.net_cash_flow_minor,
      netCashFlowText: formatCnyMinorUnits(month.net_cash_flow_minor),
      incomeHeightPercent: heightPercent(month.total_income_minor),
      spendingHeightPercent: heightPercent(month.total_spending_minor),
    })),
  };
}

export interface FeedbackListItemViewModel {
  id: string;
  content: string;
  status: FeedbackItem["status"];
  statusLabel: "待处理" | "已解决";
  createdAt: string;
  context: FeedbackItem["context"];
}

export function toFeedbackListItemViewModel(item: FeedbackItem): FeedbackListItemViewModel {
  return {
    id: item.id,
    content: item.content,
    status: item.status,
    statusLabel: item.status === "open" ? "待处理" : "已解决",
    createdAt: item.created_at,
    context: item.context,
  };
}

export interface TransactionListItemViewModel {
  id: string;
  displayName: string;
  date: string;
  typeLabel: "收入" | "支出";
  amountText: string;
  category: string;
  sourceLabel: string;
  isUnclassified: boolean;
}

export function sourceTypeLabel(sourceType: string): string {
  if (sourceType === "manual") return "手工录入";
  if (sourceType === "cmb" || sourceType === "cmb_email") return "CMB";
  return sourceType;
}

export function transactionTypeLabel(type: Transaction["type"]): "收入" | "支出" {
  return type === "income" ? "收入" : "支出";
}

export function toTransactionListItemViewModel(
  transaction: Transaction,
): TransactionListItemViewModel {
  return {
    id: transaction.id,
    displayName: transaction.enrichment.display_name,
    date: transaction.date,
    typeLabel: transactionTypeLabel(transaction.type),
    amountText: formatDecimalCurrency(transaction.amount, transaction.currency),
    category: transaction.enrichment.category,
    sourceLabel: sourceTypeLabel(transaction.source.type),
    isUnclassified: transaction.enrichment.is_unclassified,
  };
}

export function manualSourceRoleLabel(role: ManualInputRecord["source_role"]): string {
  return role === "authoritative" ? "权威来源" : "支持来源";
}

export function manualInputActionLabel(action: ManualInputAction): string {
  return {
    created: "已创建新交易",
    matched: "已匹配已有交易",
    reused: "已保留既有交易",
  }[action];
}

export function normalizeManualDescription(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/gu, "");
}

export function findSimilarManualDescriptions(
  query: string,
  descriptions: readonly string[],
  limit = 5,
): readonly string[] {
  if (!Number.isInteger(limit) || limit <= 0) {
    throw new TypeError("Manual description suggestion limit must be a positive integer");
  }
  const normalizedQuery = normalizeManualDescription(query);
  if (!normalizedQuery) {
    return [];
  }
  return descriptions
    .map((description, index) => {
      const normalized = normalizeManualDescription(description);
      const score =
        normalized === normalizedQuery
          ? 0
          : normalized.startsWith(normalizedQuery) || normalizedQuery.startsWith(normalized)
            ? 1
            : null;
      return { description, index, score };
    })
    .filter((item): item is { description: string; index: number; score: number } => item.score !== null)
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .slice(0, limit)
    .map((item) => item.description);
}

export function normalizeMerchantName(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/gu, "");
}

export function findSimilarMerchantNames(
  query: string,
  merchants: readonly MerchantMappingOption[],
  limit = 5,
): readonly string[] {
  if (!Number.isInteger(limit) || limit <= 0) {
    throw new TypeError("Merchant suggestion limit must be a positive integer");
  }
  const normalizedQuery = normalizeMerchantName(query);
  if (!normalizedQuery) {
    return [];
  }
  return merchants
    .map((merchant, index) => {
      const normalized = normalizeMerchantName(merchant.name);
      const score =
        normalized === normalizedQuery
          ? 0
          : normalized.startsWith(normalizedQuery) || normalizedQuery.startsWith(normalized)
            ? 1
            : normalized.includes(normalizedQuery) || normalizedQuery.includes(normalized)
              ? 2
              : null;
      return { name: merchant.name, index, score };
    })
    .filter((item): item is { name: string; index: number; score: number } => item.score !== null)
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .slice(0, limit)
    .map((item) => item.name);
}

export interface MappingReviewListItemViewModel {
  description: string;
  amountText: string;
  transactionCountText: string;
  latestDate: string;
  sourceTypesText: string;
  transactionOnlyExceptionCount: number;
}

export function toMappingReviewListItemViewModel(
  item: MappingReviewItem,
): MappingReviewListItemViewModel {
  return {
    description: item.description,
    amountText: formatDecimalCurrency(item.total_amount, item.currency),
    transactionCountText: `${item.transaction_count} 笔`,
    latestDate: item.latest_date,
    sourceTypesText: item.source_types.map(sourceTypeLabel).join(" + "),
    transactionOnlyExceptionCount: item.transaction_only_exception_count,
  };
}

export interface MappingReviewImpactLine {
  text: string;
  emphasis: boolean;
}

export function mappingReviewImpactLines(
  preview: MappingReviewPreview,
): readonly MappingReviewImpactLine[] {
  const lines: MappingReviewImpactLine[] = [
    {
      text: `description → Merchant：${preview.description} → ${preview.merchant}；更新 ${preview.description_affected_transaction_count} 笔仍跟随 Mapping 的交易。`,
      emphasis: true,
    },
  ];

  if (preview.is_new_merchant) {
    lines.push({
      text: `将新建 Merchant「${preview.merchant}」，默认 Category 为「${preview.category}」。`,
      emphasis: true,
    });
  } else if (preview.previous_default_category !== preview.category) {
    lines.push({
      text: `Merchant 默认 Category：${preview.previous_default_category ?? "无"} → ${preview.category}；另外更新 ${preview.default_category_affected_transaction_count} 笔当前 Merchant state。`,
      emphasis: true,
    });
  } else {
    lines.push({
      text: `Merchant「${preview.merchant}」继续使用默认 Category「${preview.category}」。`,
      emphasis: false,
    });
  }

  if (preview.preserved_merchant_exception_count > 0) {
    lines.push({
      text: `保留 ${preview.preserved_merchant_exception_count} 笔 transaction-only Merchant 例外。`,
      emphasis: false,
    });
  }
  if (preview.preserved_category_exception_count > 0) {
    lines.push({
      text: `保留 ${preview.preserved_category_exception_count} 笔显式 Category 例外。`,
      emphasis: false,
    });
  }

  lines.push({
    text: `本次预计修改 ${preview.total_affected_transaction_count} 笔 Enrichment state。`,
    emphasis: true,
  });
  return lines;
}

export function scheduledInputActionLabel(action: ScheduledInputAction): string {
  return {
    created: "创建 Transaction",
    matched: "匹配既有 Transaction",
    reused: "复用既有 Transaction",
    recovered: "恢复已生成 occurrence",
  }[action];
}

export function scheduledInputLastRunText(rule: ScheduledInputRule): string {
  if (!rule.last_occurrence_date || !rule.last_action || !rule.last_transaction_id) {
    return "尚未执行";
  }
  return `${rule.last_occurrence_date} · ${scheduledInputActionLabel(rule.last_action)} · Transaction ${rule.last_transaction_id}`;
}

export interface ScheduledInputListItemViewModel {
  id: string;
  description: string;
  enabledLabel: "启用" | "暂停";
  nextDate: string;
  typeLabel: "收入" | "支出";
  amountText: string;
  lastRunText: string;
}

export function toScheduledInputListItemViewModel(
  rule: ScheduledInputRule,
): ScheduledInputListItemViewModel {
  return {
    id: rule.id,
    description: rule.description,
    enabledLabel: rule.enabled ? "启用" : "暂停",
    nextDate: rule.next_date,
    typeLabel: transactionTypeLabel(rule.type),
    amountText: formatDecimalCurrency(rule.amount, rule.currency),
    lastRunText: scheduledInputLastRunText(rule),
  };
}
