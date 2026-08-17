import { describe, expect, it } from "vitest";

import {
  buildFeedbackList,
  feedbackActionLabel,
  feedbackStatusLabel,
  validateFeedbackDraft,
} from "../miniprogram/pages/feedback/model";
import type { FeedbackItem } from "../miniprogram/services/api";

function feedback(overrides: Partial<FeedbackItem> = {}): FeedbackItem {
  return {
    id: "feedback_1",
    created_at: "2026-08-17T10:00:00.000000Z",
    status: "open",
    content: "列表太密了",
    context: { runtime: "weapp", page: "feedback", workspace: "more" },
    ...overrides,
  };
}

describe("native Mini Feedback presentation", () => {
  it("validates a trimmed feedback command with WeChat context", () => {
    expect(validateFeedbackDraft("  希望支持更快的入口  ")).toEqual({
      ok: true,
      command: {
        content: "希望支持更快的入口",
        context: {
          runtime: "weapp",
          page: "feedback",
          workspace: "more",
        },
      },
    });
    expect(validateFeedbackDraft("   ")).toEqual({
      ok: false,
      message: "请填写反馈内容。",
    });
  });

  it("sorts recent feedback first and exposes small lifecycle labels", () => {
    const rows = buildFeedbackList([
      feedback(),
      feedback({
        id: "feedback_2",
        created_at: "2026-08-17T11:00:00.000000Z",
        status: "resolved",
        content: "已经处理",
      }),
    ]);

    expect(rows.map((item) => item.id)).toEqual(["feedback_2", "feedback_1"]);
    expect(rows[0]).toMatchObject({
      statusLabel: "已解决",
      actionLabel: "重新打开",
      contextText: "more · feedback · 微信小程序",
    });
    expect(feedbackStatusLabel("open")).toBe("待处理");
    expect(feedbackActionLabel("open")).toBe("标记已解决");
  });
});