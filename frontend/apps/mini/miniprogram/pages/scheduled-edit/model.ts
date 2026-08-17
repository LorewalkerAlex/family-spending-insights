import type {
  ScheduledInputCommand,
  ScheduledInputRule,
  TransactionType,
} from "../../services/api";

export interface ScheduledDraft {
  type: TransactionType;
  amount: string;
  description: string;
  nextDate: string;
  note: string;
  enabled: boolean;
}

export type ScheduledDraftValidation =
  | { ok: true; command: ScheduledInputCommand }
  | { ok: false; message: string };

function isoLocalDate(year: number, monthIndex: number, day: number): string {
  return `${year}-${String(monthIndex + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export function defaultScheduledDate(now = new Date()): string {
  if (now.getDate() <= 28) {
    return isoLocalDate(now.getFullYear(), now.getMonth(), now.getDate());
  }
  const nextMonth = new Date(now.getFullYear(), now.getMonth() + 1, 1);
  return isoLocalDate(nextMonth.getFullYear(), nextMonth.getMonth(), 1);
}

function isValidScheduledDate(value: string): boolean {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return false;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (day < 1 || day > 28) {
    return false;
  }
  const candidate = new Date(year, month - 1, day);
  return (
    candidate.getFullYear() === year &&
    candidate.getMonth() === month - 1 &&
    candidate.getDate() === day
  );
}

function isPositiveDecimal(value: string): boolean {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) {
    return false;
  }
  return /[1-9]/.test(`${match[1] ?? ""}${match[2] ?? ""}`);
}

export function validateScheduledDraft(draft: ScheduledDraft): ScheduledDraftValidation {
  const amount = draft.amount.trim();
  const description = draft.description.trim();
  const note = draft.note.trim();
  const nextDate = draft.nextDate.trim();

  if (!/^-?\d+(?:\.\d+)?$/.test(amount)) {
    return { ok: false, message: "金额请输入数字，例如 88.50。" };
  }
  if (draft.type === "income" && !isPositiveDecimal(amount)) {
    return { ok: false, message: "收入金额必须大于 0。" };
  }
  if (!description) {
    return { ok: false, message: "请填写规则描述。" };
  }
  if (!isValidScheduledDate(nextDate)) {
    return { ok: false, message: "下次日期必须是有效日期，且 V1 仅支持每月 1–28 日。" };
  }

  return {
    ok: true,
    command: {
      type: draft.type,
      amount,
      description,
      next_date: nextDate,
      note: note || null,
      enabled: draft.enabled,
    },
  };
}

export function draftFromRule(rule: ScheduledInputRule): ScheduledDraft {
  return {
    type: rule.type,
    amount: rule.amount,
    description: rule.description,
    nextDate: rule.next_date,
    note: rule.note ?? "",
    enabled: rule.enabled,
  };
}

export function scheduledSaveMessage(rule: ScheduledInputRule): string {
  if (rule.last_occurrence_date) {
    return `规则已保存；最近执行 ${rule.last_occurrence_date}，下一次 ${rule.next_date}。`;
  }
  return `规则已保存；下一次 ${rule.next_date}。`;
}