import {
  formatFullTransactionDate,
  formatTransactionMoney,
} from "../../presentation/transaction";
import type {
  MappingReviewPreview,
  MappingReviewWorkspace,
  Transaction,
} from "../../services/api";

export interface ReviewRepresentativeTransaction {
  id: string;
  amountText: string;
  dateText: string;
  sourceText: string;
}

export interface ReviewDetailViewModel {
  description: string;
  transactionCount: number;
  transactionCountText: string;
  totalText: string;
  latestDateText: string;
  sourceText: string;
  exceptionCount: number;
  hasExceptions: boolean;
  exceptionText: string;
  merchants: MappingReviewWorkspace["merchants"];
  categories: string[];
  representatives: ReviewRepresentativeTransaction[];
}

export interface ReviewPreviewViewModel {
  previewMerchant: string;
  previewCategory: string;
  isNewMerchant: boolean;
  merchantModeText: string;
  changesExistingMerchantDefault: boolean;
  previousCategoryText: string;
  descriptionTransactionCountText: string;
  descriptionAffectedText: string;
  defaultCategoryAffectedText: string;
  totalAffectedText: string;
  preservedMerchantExceptionText: string;
  preservedCategoryExceptionText: string;
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

export function decodeReviewDescriptionQuery(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  try {
    return decodeURIComponent(trimmed).trim();
  } catch {
    return trimmed;
  }
}

export function normalizeMerchantName(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/gu, "");
}

/** Suggest only exact/prefix merchant matches; final Mapping validity remains Backend-owned. */
export function findMerchantSuggestions(
  query: string,
  merchants: readonly MappingReviewWorkspace["merchants"][number][],
  limit = 5,
): MappingReviewWorkspace["merchants"] {
  const normalizedQuery = normalizeMerchantName(query);
  if (!normalizedQuery || !Number.isInteger(limit) || limit <= 0) {
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
            : null;
      return { merchant, index, score };
    })
    .filter(
      (item): item is {
        merchant: MappingReviewWorkspace["merchants"][number];
        index: number;
        score: number;
      } => item.score !== null,
    )
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .slice(0, limit)
    .map((item) => item.merchant);
}

/** Build one mobile review detail from the current workspace plus representative transactions. */
export function buildReviewDetailViewModel(
  description: string,
  workspace: MappingReviewWorkspace,
  transactions: readonly Transaction[],
  representativeLimit = 3,
): ReviewDetailViewModel | null {
  const item = workspace.items.find((candidate) => candidate.description === description);
  if (!item) {
    return null;
  }

  const representatives = transactions
    .map((transaction, sourceIndex) => ({ transaction, sourceIndex }))
    .filter(
      ({ transaction }) =>
        transaction.type === "expense" && transaction.source.description === description,
    )
    .sort((left, right) => {
      const dateOrder = right.transaction.date.localeCompare(left.transaction.date);
      return dateOrder !== 0 ? dateOrder : right.sourceIndex - left.sourceIndex;
    })
    .slice(0, representativeLimit)
    .map(({ transaction }) => ({
      id: transaction.id,
      amountText: formatTransactionMoney(transaction.amount, transaction.type),
      dateText: formatFullTransactionDate(transaction.date),
      sourceText: sourceTypeLabel(transaction.source.type),
    }));

  return {
    description: item.description,
    transactionCount: item.transaction_count,
    transactionCountText: `${item.transaction_count} 笔`,
    totalText: formatTransactionMoney(item.total_amount, "expense"),
    latestDateText: formatFullTransactionDate(item.latest_date),
    sourceText: item.source_types.map(sourceTypeLabel).join(" · "),
    exceptionCount: item.transaction_only_exception_count,
    hasExceptions: item.transaction_only_exception_count > 0,
    exceptionText:
      item.transaction_only_exception_count > 0
        ? `${item.transaction_only_exception_count} 笔已有单笔商户例外，Apply 时会保留。`
        : "",
    merchants: workspace.merchants,
    categories: workspace.categories,
    representatives,
  };
}

/** Turn Backend impact counters into explicit mobile confirmation copy. */
export function buildReviewPreviewViewModel(
  preview: MappingReviewPreview,
): ReviewPreviewViewModel {
  const changesExistingMerchantDefault =
    !preview.is_new_merchant &&
    preview.previous_default_category !== null &&
    preview.previous_default_category !== preview.category;

  return {
    previewMerchant: preview.merchant,
    previewCategory: preview.category,
    isNewMerchant: preview.is_new_merchant,
    merchantModeText: preview.is_new_merchant
      ? "将创建新商户"
      : changesExistingMerchantDefault
        ? "将修改已有商户默认分类"
        : "将复用已有商户",
    changesExistingMerchantDefault,
    previousCategoryText: preview.previous_default_category ?? "",
    descriptionTransactionCountText: `${preview.description_transaction_count} 笔属于当前 description`,
    descriptionAffectedText: `${preview.description_affected_transaction_count} 笔当前 description 会发生变化`,
    defaultCategoryAffectedText:
      preview.default_category_affected_transaction_count > 0
        ? `另有 ${preview.default_category_affected_transaction_count} 笔该商户交易会随默认分类变化`
        : "不会额外改变其他交易的默认分类",
    totalAffectedText: `合计影响 ${preview.total_affected_transaction_count} 笔交易`,
    preservedMerchantExceptionText:
      preview.preserved_merchant_exception_count > 0
        ? `${preview.preserved_merchant_exception_count} 笔单笔商户例外会保留`
        : "没有单笔商户例外需要保留",
    preservedCategoryExceptionText:
      preview.preserved_category_exception_count > 0
        ? `${preview.preserved_category_exception_count} 笔单笔分类例外会保留`
        : "没有单笔分类例外需要保留",
  };
}