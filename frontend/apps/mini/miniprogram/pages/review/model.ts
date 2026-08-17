import {
  formatFullTransactionDate,
  formatTransactionMoney,
} from "../../presentation/transaction";
import type { MappingReviewWorkspace } from "../../services/api";

export interface ReviewListItemViewModel {
  description: string;
  transactionCount: number;
  transactionCountText: string;
  totalText: string;
  latestDateText: string;
  sourceText: string;
  hasExceptions: boolean;
  exceptionText: string;
}

export interface ReviewListViewModel {
  reviewCount: number;
  reviewCountText: string;
  isEmpty: boolean;
  items: ReviewListItemViewModel[];
}

function sourceTypeLabel(value: string): string {
  if (value === "cmb_email" || value === "cmb") {
    return "招商银行邮件账单";
  }
  if (value === "manual") {
    return "手工录入";
  }
  return value;
}

/** Present Backend-owned Mapping Review groups without changing their ordering or identity. */
export function buildReviewListViewModel(
  workspace: MappingReviewWorkspace,
): ReviewListViewModel {
  const items = workspace.items.map((item) => {
    const hasExceptions = item.transaction_only_exception_count > 0;
    return {
      description: item.description,
      transactionCount: item.transaction_count,
      transactionCountText: `${item.transaction_count} 笔`,
      totalText: formatTransactionMoney(item.total_amount, "expense"),
      latestDateText: formatFullTransactionDate(item.latest_date),
      sourceText: item.source_types.map(sourceTypeLabel).join(" · "),
      hasExceptions,
      exceptionText: hasExceptions
        ? `${item.transaction_only_exception_count} 笔保留单笔商户例外`
        : "",
    };
  });

  return {
    reviewCount: items.length,
    reviewCountText: items.length > 0 ? `${items.length} 项待审核` : "暂无待审核",
    isEmpty: items.length === 0,
    items,
  };
}