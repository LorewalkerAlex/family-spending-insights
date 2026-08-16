import type {
  CreateManualInputCommand,
  ManualInputAction,
  TransactionType,
} from "../../services/api";

export interface ManualDraft {
  type: TransactionType;
  date: string;
  amount: string;
  description: string;
  note: string;
}

export interface DescriptionAssist {
  suggestions: string[];
  normalizedDuplicate: string;
  hasExactExisting: boolean;
  hasNormalizedDuplicate: boolean;
}

export type ManualDraftValidation =
  | {
      ok: true;
      command: CreateManualInputCommand;
    }
  | {
      ok: false;
      message: string;
    };

/** Format a local calendar day without letting UTC conversion move the selected date. */
export function todayIsoDate(now = new Date()): string {
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** Match the existing Web entry affordance: trim, case-fold, then ignore whitespace only. */
export function normalizeManualDescription(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/gu, "");
}

/** Offer only exact/prefix history candidates; this deliberately avoids aggressive fuzzy merging. */
export function findSimilarManualDescriptions(
  query: string,
  descriptions: readonly string[],
  limit = 5,
): string[] {
  const normalizedQuery = normalizeManualDescription(query);
  if (!normalizedQuery || !Number.isInteger(limit) || limit <= 0) {
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
    .filter(
      (item): item is { description: string; index: number; score: number } =>
        item.score !== null,
    )
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .slice(0, limit)
    .map((item) => item.description);
}

/** Describe whether current text can safely reuse history or needs an explicit new-text decision. */
export function buildDescriptionAssist(
  query: string,
  descriptions: readonly string[],
): DescriptionAssist {
  const trimmed = query.trim();
  const normalized = normalizeManualDescription(trimmed);
  const normalizedDuplicate = normalized
    ? descriptions.find((item) => normalizeManualDescription(item) === normalized) ?? ""
    : "";

  return {
    suggestions: findSimilarManualDescriptions(query, descriptions).filter(
      (item) => item !== trimmed,
    ),
    normalizedDuplicate,
    hasExactExisting: normalizedDuplicate !== "" && normalizedDuplicate === trimmed,
    hasNormalizedDuplicate: normalizedDuplicate !== "" && normalizedDuplicate !== trimmed,
  };
}

function isValidIsoDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return false;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const candidate = new Date(year, month - 1, day);
  return (
    candidate.getFullYear() === year &&
    candidate.getMonth() === month - 1 &&
    candidate.getDate() === day
  );
}

function isPositiveDecimal(amount: string): boolean {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
  if (!match || match[1] === "-") {
    return false;
  }
  const digits = `${match[2] ?? ""}${match[3] ?? ""}`;
  return /[1-9]/.test(digits);
}

/** Validate only transport/UI invariants; reconciliation and classification stay Backend-owned. */
export function validateManualDraft(
  draft: ManualDraft,
  descriptions: readonly string[],
  confirmedNewDescription: string,
): ManualDraftValidation {
  const date = draft.date.trim();
  const amount = draft.amount.trim();
  const description = draft.description.trim();
  const note = draft.note.trim();

  if (!isValidIsoDate(date)) {
    return { ok: false, message: "请选择有效日期。" };
  }
  if (!/^-?\d+(?:\.\d+)?$/.test(amount)) {
    return { ok: false, message: "金额请输入数字，例如 88.50。" };
  }
  if (draft.type === "income" && !isPositiveDecimal(amount)) {
    return { ok: false, message: "收入金额必须大于 0。" };
  }
  if (!description) {
    return { ok: false, message: "请填写交易描述。" };
  }

  const assist = buildDescriptionAssist(description, descriptions);
  if (
    assist.hasNormalizedDuplicate &&
    confirmedNewDescription !== description
  ) {
    return {
      ok: false,
      message: `发现非常接近的历史描述「${assist.normalizedDuplicate}」。请复用它，或明确按当前文本新建。`,
    };
  }

  return {
    ok: true,
    command: {
      type: draft.type,
      date,
      amount,
      description,
      note: note || null,
    },
  };
}

/** Surface Backend reconciliation outcome without asking the Mini to infer what happened. */
export function manualInputActionLabel(action: ManualInputAction): string {
  return {
    created: "已创建新交易",
    matched: "已匹配已有交易",
    reused: "已保留既有交易",
  }[action];
}

/** Keep a successful new source description available for the next quick-entry suggestion round. */
export function mergeManualDescription(
  descriptions: readonly string[],
  description: string,
): string[] {
  return descriptions.includes(description)
    ? [...descriptions]
    : [...descriptions, description];
}
