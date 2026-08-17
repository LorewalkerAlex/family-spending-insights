import { describe, expect, it } from "vitest";

import {
  buildScheduledList,
  buildScheduledSummary,
  scheduledRunMessage,
} from "../miniprogram/pages/scheduled/model";
import {
  defaultScheduledDate,
  draftFromRule,
  validateScheduledDraft,
} from "../miniprogram/pages/scheduled-edit/model";
import type { ScheduledInputRule } from "../miniprogram/services/api";

function rule(overrides: Partial<ScheduledInputRule> = {}): ScheduledInputRule {
  return {
    id: "schedule_1",
    enabled: true,
    type: "expense",
    amount: "1200",
    currency: "CNY",
    description: "固定房租",
    note: "月租",
    next_date: "2026-09-05",
    last_occurrence_date: null,
    last_source_record_id: null,
    last_transaction_id: null,
    last_action: null,
    ...overrides,
  };
}

describe("native Mini Scheduled Input presentation", () => {
  it("uses today when recurrence day is safe and next month day 1 after day 28", () => {
    expect(defaultScheduledDate(new Date(2026, 7, 16))).toBe("2026-08-16");
    expect(defaultScheduledDate(new Date(2026, 7, 31))).toBe("2026-09-01");
  });

  it("validates the V1 monthly rule contract without inventing reconciliation rules", () => {
    expect(
      validateScheduledDraft({
        type: "expense",
        amount: "88.50",
        description: " 固定支出 ",
        nextDate: "2026-09-18",
        note: " 备注 ",
        enabled: true,
      }),
    ).toEqual({
      ok: true,
      command: {
        type: "expense",
        amount: "88.50",
        description: "固定支出",
        next_date: "2026-09-18",
        note: "备注",
        enabled: true,
      },
    });

    expect(
      validateScheduledDraft({
        type: "expense",
        amount: "88.50",
        description: "固定支出",
        nextDate: "2026-08-31",
        note: "",
        enabled: true,
      }),
    ).toMatchObject({ ok: false });
  });

  it("builds list and execution summaries from Backend rule state", () => {
    const rules = [
      rule(),
      rule({
        id: "schedule_2",
        enabled: false,
        type: "income",
        amount: "10000",
        description: "工资",
        next_date: "2026-09-01",
        last_occurrence_date: "2026-08-01",
        last_source_record_id: "source_2",
        last_transaction_id: "txn_2",
        last_action: "created",
      }),
    ];

    expect(buildScheduledSummary(rules)).toEqual({
      totalCount: 2,
      enabledCount: 1,
      pausedCount: 1,
    });
    expect(buildScheduledList(rules)[0]).toMatchObject({
      id: "schedule_2",
      enabledLabel: "暂停",
      typeLabel: "收入",
      amountText: "+¥10,000.00",
      lastRunText: "8月1日 · 创建交易",
    });
    expect(draftFromRule(rules[0]!)).toMatchObject({
      description: "固定房租",
      nextDate: "2026-09-05",
    });
    expect(scheduledRunMessage({ generated_count: 0, occurrences: [] })).toBe(
      "当前没有需要执行的到期项。",
    );
  });
});