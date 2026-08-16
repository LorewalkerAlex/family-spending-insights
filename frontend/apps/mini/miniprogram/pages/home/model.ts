import type {
  FinancialSummary,
  FinancialSummaryMonth,
  MappingReviewWorkspace,
  Transaction,
} from "../../services/api";

export interface HomeTransactionItem {
  id: string;
  name: string;
  category: string;
  dateText: string;
  amountText: string;
  amountTone: "income" | "expense";
}

export interface HomeViewModel {
  hasSummary: boolean;
  monthLabel: string;
  spendingText: string;
  incomeText: string;
  netText: string;
  netTone: "positive" | "negative" | "neutral";
  reviewCount: number;
  reviewTitle: string;
  reviewBody: string;
  recentTransactions: HomeTransactionItem[];
  isCompletelyEmpty: boolean;
}

/** Format integer minor currency units without depending on locale support in the Mini runtime. */
export function formatMinorMoney(minor: number): string {
  const negative = minor < 0;
  const absolute = Math.abs(Math.trunc(minor));
  const yuan = Math.floor(absolute / 100);
  const cents = String(absolute % 100).padStart(2, "0");
  const grouped = String(yuan).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}¥${grouped}.${cents}`;
}

/** Format canonical decimal transaction text while preserving negative adjustments/refunds. */
export function formatTransactionMoney(amount: string, type: Transaction["type"]): string {
  const match = amount.trim().match(/^([-+]?)(\d+)(?:\.(\d+))?$/);
  if (!match) {
    return amount;
  }

  const sourceNegative = match[1] === "-";
  const whole = match[2] ?? "0";
  const fraction = `${match[3] ?? ""}00`.slice(0, 2);
  const grouped = whole.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const normalDirection = type === "expense" ? -1 : 1;
  const displayDirection = sourceNegative ? -normalDirection : normalDirection;
  return `${displayDirection < 0 ? "-" : "+"}¥${grouped}.${fraction}`;
}

/** Convert canonical YYYY-MM text into the concise month label used by Home. */
export function formatMonth(value: string): string {
  const match = value.match(/^(\d{4})-(\d{2})$/);
  if (!match) {
    return value;
  }
  return `${match[1]} 年 ${Number(match[2])} 月`;
}

/** Convert canonical YYYY-MM-DD text into a short transaction-list date label. */
export function formatTransactionDate(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})$/);
  if (!match) {
    return value;
  }
  return `${Number(match[1])}月${Number(match[2])}日`;
}

/** Pick the newest month that Backend marked as visible, independent of payload ordering. */
export function latestVisibleMonth(months: readonly FinancialSummaryMonth[]): FinancialSummaryMonth | null {
  const visible = months.filter((item) => item.show);
  if (visible.length === 0) {
    return null;
  }
  return visible.reduce((latest, item) => (item.month > latest.month ? item : latest));
}

/** Build a stable recent-transaction list using date first and source order only as a same-day tie-break. */
export function recentTransactionItems(
  transactions: readonly Transaction[],
  limit = 5,
): HomeTransactionItem[] {
  return transactions
    .map((transaction, index) => ({ transaction, index }))
    .sort((left, right) => {
      const dateOrder = right.transaction.date.localeCompare(left.transaction.date);
      return dateOrder !== 0 ? dateOrder : right.index - left.index;
    })
    .slice(0, limit)
    .map(({ transaction }) => {
      const name =
        transaction.enrichment.display_name ||
        transaction.enrichment.merchant ||
        transaction.source.description ||
        "未命名交易";
      const category = transaction.enrichment.category || "待分类";
      return {
        id: transaction.id,
        name,
        category,
        dateText: formatTransactionDate(transaction.date),
        amountText: formatTransactionMoney(transaction.amount, transaction.type),
        amountTone: transaction.type,
      };
    });
}

/** Join Backend query results into the presentation-only state consumed by the Home page. */
export function buildHomeViewModel(
  summary: FinancialSummary,
  transactions: readonly Transaction[],
  review: MappingReviewWorkspace,
): HomeViewModel {
  const month = latestVisibleMonth(summary.months);
  const recentTransactions = recentTransactionItems(transactions);
  const reviewCount = review.items.length;
  const hasSummary = month !== null;

  return {
    hasSummary,
    monthLabel: month ? formatMonth(month.month) : "暂无完整月份",
    spendingText: month ? formatMinorMoney(month.total_spending_minor) : "—",
    incomeText: month ? formatMinorMoney(month.total_income_minor) : "—",
    netText: month ? formatMinorMoney(month.net_cash_flow_minor) : "—",
    netTone: month
      ? month.net_cash_flow_minor > 0
        ? "positive"
        : month.net_cash_flow_minor < 0
          ? "negative"
          : "neutral"
      : "neutral",
    reviewCount,
    reviewTitle: reviewCount > 0 ? `${reviewCount} 个待审核` : "暂无待审核",
    reviewBody:
      reviewCount > 0
        ? "有新的交易描述需要确认商户或分类。"
        : "当前没有需要处理的 Mapping Review。",
    recentTransactions,
    isCompletelyEmpty: !hasSummary && recentTransactions.length === 0 && reviewCount === 0,
  };
}
