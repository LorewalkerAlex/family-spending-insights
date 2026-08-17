import { formatTransactionMoney } from "../../presentation/transaction";
import type {
  ScheduledInputAction,
  ScheduledInputRule,
  ScheduledInputRun,
} from "../../services/api";

export interface ScheduledListItem {
  id: string;
  description: string;
  enabled: boolean;
  enabledLabel: string;
  typeLabel: string;
  amountText: string;
  nextDateText: string;
  lastRunText: string;
}

export interface ScheduledSummary {
  totalCount: number;
  enabledCount: number;
  pausedCount: number;
}

function shortDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) {
    return value;
  }
  return `${Number(match[2])}月${Number(match[3])}日`;
}

export function scheduledActionLabel(action: ScheduledInputAction): string {
  return {
    created: "创建交易",
    matched: "匹配已有交易",
    reused: "复用已有交易",
    recovered: "恢复已生成记录",
  }[action];
}

export function scheduledLastRunText(rule: ScheduledInputRule): string {
  if (!rule.last_occurrence_date || !rule.last_action) {
    return "尚未执行";
  }
  return `${shortDate(rule.last_occurrence_date)} · ${scheduledActionLabel(rule.last_action)}`;
}

export function buildScheduledList(rules: readonly ScheduledInputRule[]): ScheduledListItem[] {
  return [...rules]
    .sort((left, right) => left.next_date.localeCompare(right.next_date) || left.description.localeCompare(right.description))
    .map((rule) => ({
      id: rule.id,
      description: rule.description,
      enabled: rule.enabled,
      enabledLabel: rule.enabled ? "启用" : "暂停",
      typeLabel: rule.type === "income" ? "收入" : "支出",
      amountText: formatTransactionMoney(rule.amount, rule.type),
      nextDateText: shortDate(rule.next_date),
      lastRunText: scheduledLastRunText(rule),
    }));
}

export function buildScheduledSummary(rules: readonly ScheduledInputRule[]): ScheduledSummary {
  const enabledCount = rules.filter((rule) => rule.enabled).length;
  return {
    totalCount: rules.length,
    enabledCount,
    pausedCount: rules.length - enabledCount,
  };
}

export function scheduledRunMessage(result: ScheduledInputRun): string {
  if (result.generated_count === 0) {
    return "当前没有需要执行的到期项。";
  }
  return `已处理 ${result.generated_count} 个到期项；相关交易和统计会从 Backend 重新读取。`;
}