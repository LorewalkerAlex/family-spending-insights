import type { Transaction } from "../services/api";

/** Prefer reviewed display text while preserving the source description as the final fallback. */
export function transactionDisplayName(transaction: Transaction): string {
  return (
    transaction.enrichment.display_name ||
    transaction.enrichment.merchant ||
    transaction.source.description ||
    "未命名交易"
  );
}

/** Keep the Backend category authoritative and only provide a presentation fallback when absent. */
export function transactionDisplayCategory(transaction: Transaction): string {
  return transaction.enrichment.category || "待分类";
}

/** Format canonical decimal transaction text while preserving refunds/reversals as opposite flows. */
export function formatTransactionMoney(amount: string, type: Transaction["type"]): string {
  const match = amount.trim().match(/^([-+]?)(\d+)(?:\.(\d+))?$/);
  if (!match) {
    return amount;
  }

  const sourceNegative = match[1] === "-";
  const whole = match[2] ?? "0";
  const fraction = `${match[3] ?? ""}00`.slice(0, 2);
  const grouped = whole
    .replace(/^0+(?=\d)/, "")
    .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const normalDirection = type === "expense" ? -1 : 1;
  const displayDirection = sourceNegative ? -normalDirection : normalDirection;
  return `${displayDirection < 0 ? "-" : "+"}¥${grouped}.${fraction}`;
}

/** Derive only the visual amount direction from the already-authoritative transaction facts. */
export function transactionAmountTone(transaction: Transaction): "positive" | "negative" {
  return formatTransactionMoney(transaction.amount, transaction.type).startsWith("+")
    ? "positive"
    : "negative";
}

/** Convert canonical YYYY-MM text into the concise month label shared by Mini finance views. */
export function formatMonthLabel(value: string): string {
  const match = value.match(/^(\d{4})-(\d{2})$/);
  if (!match) {
    return value;
  }
  return `${match[1]} 年 ${Number(match[2])} 月`;
}

/** Convert canonical YYYY-MM-DD text into a short list label. */
export function formatShortTransactionDate(value: string): string {
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})$/);
  if (!match) {
    return value;
  }
  return `${Number(match[1])}月${Number(match[2])}日`;
}

/** Convert canonical YYYY-MM-DD text into the explicit detail-page label. */
export function formatFullTransactionDate(value: string): string {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) {
    return value;
  }
  return `${match[1]} 年 ${Number(match[2])} 月 ${Number(match[3])} 日`;
}
