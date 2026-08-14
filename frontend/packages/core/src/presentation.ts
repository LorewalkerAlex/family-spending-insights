import type {
  FeedbackItem,
  FinancialSummary,
  ManualInputAction,
  ManualInputRecord,
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
  totalIncomeText: string;
  totalSpendingText: string;
  netCashFlowText: string;
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
        totalIncomeText: formatCnyMinorUnits(month.total_income_minor),
        totalSpendingText: formatCnyMinorUnits(month.total_spending_minor),
        netCashFlowText: formatCnyMinorUnits(month.net_cash_flow_minor),
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
  return sourceType === "manual" ? "手工录入" : sourceType;
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
