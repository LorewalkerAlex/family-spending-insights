import type { FeedbackItem, FinancialSummary } from "./contracts";

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

export function toFinancialSummaryViewModel(
  summary: FinancialSummary,
): FinancialSummaryViewModel {
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
