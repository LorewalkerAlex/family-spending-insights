import {
  formatFullTransactionDate,
  formatTransactionMoney,
  transactionAmountTone,
  transactionDisplayCategory,
  transactionDisplayName,
} from "../../presentation/transaction";
import type { Transaction } from "../../services/api";

export interface TransactionDetailViewModel {
  id: string;
  typeLabel: string;
  amountText: string;
  amountTone: "positive" | "negative";
  dateText: string;
  name: string;
  merchantText: string;
  categoryText: string;
  categorySourceText: string;
  rawDescription: string;
  sourceText: string;
  hasNote: boolean;
  noteText: string;
  isUnclassified: boolean;
  hasReviewSignals: boolean;
  reviewSignals: string[];
}

function sourceLabel(sourceType: string): string {
  if (sourceType === "cmb_email") {
    return "招商银行邮件账单";
  }
  if (sourceType === "manual") {
    return "手工录入";
  }
  return sourceType;
}

function categorySourceLabel(categorySource: string | null): string {
  switch (categorySource) {
    case "merchant_default":
      return "商户默认分类";
    case "transaction_override":
      return "单笔调整";
    case "income_default":
      return "收入默认分类";
    case "unclassified":
      return "待分类";
    default:
      return categorySource || "—";
  }
}

function reviewSignalLabel(signal: string): string {
  switch (signal) {
    case "other_expense_review":
      return "其他支出需要复核";
    case "high_value_general_shopping_review":
      return "高额综合购物需要复核";
    default:
      return signal;
  }
}

/** Build the read-only mobile detail presentation from one Canonical Transaction DTO. */
export function buildTransactionDetailViewModel(
  transaction: Transaction,
): TransactionDetailViewModel {
  const noteText = transaction.enrichment.note?.trim() ?? "";
  const reviewSignals = [
    ...(transaction.enrichment.is_unclassified ? ["待完成商户 / 分类审核"] : []),
    ...transaction.enrichment.review_signals.map(reviewSignalLabel),
  ];

  return {
    id: transaction.id,
    typeLabel: transaction.type === "expense" ? "支出" : "收入",
    amountText: formatTransactionMoney(transaction.amount, transaction.type),
    amountTone: transactionAmountTone(transaction),
    dateText: formatFullTransactionDate(transaction.date),
    name: transactionDisplayName(transaction),
    merchantText:
      transaction.enrichment.merchant ||
      (transaction.type === "income" ? "不适用" : "未识别"),
    categoryText: transactionDisplayCategory(transaction),
    categorySourceText: categorySourceLabel(transaction.enrichment.category_source),
    rawDescription: transaction.source.description || "无原始描述",
    sourceText: sourceLabel(transaction.source.type),
    hasNote: noteText.length > 0,
    noteText,
    isUnclassified: transaction.enrichment.is_unclassified,
    hasReviewSignals: reviewSignals.length > 0,
    reviewSignals,
  };
}
