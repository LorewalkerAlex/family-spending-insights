import {
  formatMonthLabel,
  formatTransactionMoney,
  transactionAmountTone,
  transactionDisplayCategory,
  transactionDisplayName,
} from "../../presentation/transaction";
import type { Transaction } from "../../services/api";

export type TransactionFilter = "all" | "expense" | "income";

export interface TransactionMonthOption {
  value: string;
  label: string;
}

export interface TransactionListItem {
  id: string;
  name: string;
  category: string;
  amountText: string;
  amountTone: "positive" | "negative";
  isUnclassified: boolean;
}

export interface TransactionDayGroup {
  date: string;
  dateLabel: string;
  count: number;
  items: TransactionListItem[];
}

export interface TransactionsViewModel {
  monthOptions: TransactionMonthOption[];
  selectedMonth: string;
  selectedMonthIndex: number;
  selectedMonthLabel: string;
  filter: TransactionFilter;
  groups: TransactionDayGroup[];
  transactionCount: number;
  isCompletelyEmpty: boolean;
  isFilteredEmpty: boolean;
}

/** List only canonical YYYY-MM values present in transaction facts, newest first. */
export function transactionMonthOptions(
  transactions: readonly Transaction[],
): TransactionMonthOption[] {
  const months = new Set<string>();
  for (const transaction of transactions) {
    const match = transaction.date.match(/^(\d{4}-\d{2})-\d{2}$/);
    if (match?.[1]) {
      months.add(match[1]);
    }
  }
  return Array.from(months)
    .sort((left, right) => right.localeCompare(left))
    .map((value) => ({ value, label: formatMonthLabel(value) }));
}

function shortDayLabel(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})$/);
  if (!match) {
    return value;
  }
  return `${Number(match[1])} 月 ${Number(match[2])} 日`;
}

/** Build the month/type-filtered, date-grouped list without changing Backend transaction facts. */
export function buildTransactionsViewModel(
  transactions: readonly Transaction[],
  requestedMonth: string | null = null,
  filter: TransactionFilter = "all",
): TransactionsViewModel {
  const monthOptions = transactionMonthOptions(transactions);
  const requestedIndex = requestedMonth
    ? monthOptions.findIndex((option) => option.value === requestedMonth)
    : -1;
  const selectedMonthIndex = requestedIndex >= 0 ? requestedIndex : 0;
  const selectedMonth = monthOptions[selectedMonthIndex]?.value ?? "";
  const selectedMonthLabel = monthOptions[selectedMonthIndex]?.label ?? "暂无月份";

  const filtered = transactions
    .map((transaction, sourceIndex) => ({ transaction, sourceIndex }))
    .filter(({ transaction }) => {
      if (!selectedMonth || !transaction.date.startsWith(`${selectedMonth}-`)) {
        return false;
      }
      return filter === "all" || transaction.type === filter;
    })
    .sort((left, right) => {
      const dateOrder = right.transaction.date.localeCompare(left.transaction.date);
      return dateOrder !== 0 ? dateOrder : right.sourceIndex - left.sourceIndex;
    });

  const groups: TransactionDayGroup[] = [];
  for (const { transaction } of filtered) {
    let group = groups[groups.length - 1];
    if (!group || group.date !== transaction.date) {
      group = {
        date: transaction.date,
        dateLabel: shortDayLabel(transaction.date),
        count: 0,
        items: [],
      };
      groups.push(group);
    }
    group.items.push({
      id: transaction.id,
      name: transactionDisplayName(transaction),
      category: transactionDisplayCategory(transaction),
      amountText: formatTransactionMoney(transaction.amount, transaction.type),
      amountTone: transactionAmountTone(transaction),
      isUnclassified: transaction.enrichment.is_unclassified,
    });
    group.count += 1;
  }

  return {
    monthOptions,
    selectedMonth,
    selectedMonthIndex,
    selectedMonthLabel,
    filter,
    groups,
    transactionCount: filtered.length,
    isCompletelyEmpty: transactions.length === 0,
    isFilteredEmpty: transactions.length > 0 && filtered.length === 0,
  };
}
