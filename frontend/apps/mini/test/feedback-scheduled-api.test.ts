import { describe, expect, it, vi } from "vitest";

import {
  createFamilySpendingApi,
  type RequestOptions,
  type Requester,
  type ScheduledInputRule,
} from "../miniprogram/services/api";

function requesterFor(
  responses: Record<string, { statusCode: number; data: unknown }>,
): { calls: RequestOptions[]; requester: Requester } {
  const calls: RequestOptions[] = [];
  const requester: Requester = (options) => {
    calls.push(options);
    const response = responses[`${options.method} ${options.url}`];
    if (!response) {
      options.fail({ errMsg: "request:fail missing fixture" });
      return undefined;
    }
    options.success(response);
    return undefined;
  };
  return { calls, requester: vi.fn(requester) };
}

function feedbackPayload(status: "open" | "resolved" = "open") {
  return {
    id: "feedback_1",
    created_at: "2026-08-17T10:00:00.000000Z",
    status,
    content: "Mini feedback",
    context: {
      runtime: "weapp",
      page: "feedback",
      workspace: "more",
    },
  };
}

function scheduledPayload(overrides: Partial<ScheduledInputRule> = {}) {
  return {
    id: "schedule_1",
    enabled: true,
    type: "expense",
    amount: "88.50",
    currency: "CNY",
    description: "固定支出",
    note: null,
    next_date: "2026-09-18",
    last_occurrence_date: null,
    last_source_record_id: null,
    last_transaction_id: null,
    last_action: null,
    ...overrides,
  };
}

describe("native Mini Feedback and Scheduled Input API", () => {
  it("uses the canonical Feedback list/create/status routes", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const { calls, requester } = requesterFor({
      [`GET ${baseUrl}/api/feedback`]: {
        statusCode: 200,
        data: { feedback: [feedbackPayload()] },
      },
      [`POST ${baseUrl}/api/feedback`]: {
        statusCode: 201,
        data: { feedback: feedbackPayload() },
      },
      [`PATCH ${baseUrl}/api/feedback/feedback_1`]: {
        statusCode: 200,
        data: { feedback: feedbackPayload("resolved") },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(api.feedback()).resolves.toMatchObject([{ id: "feedback_1", status: "open" }]);
    await expect(
      api.createFeedback({
        content: "Mini feedback",
        context: { runtime: "weapp", page: "feedback", workspace: "more" },
      }),
    ).resolves.toMatchObject({ id: "feedback_1", context: { runtime: "weapp" } });
    await expect(api.updateFeedbackStatus("feedback_1", "resolved")).resolves.toMatchObject({
      status: "resolved",
    });

    expect(calls.map((call) => [call.method, call.url])).toEqual([
      ["GET", `${baseUrl}/api/feedback`],
      ["POST", `${baseUrl}/api/feedback`],
      ["PATCH", `${baseUrl}/api/feedback/feedback_1`],
    ]);
    expect(calls[1]?.data).toEqual({
      content: "Mini feedback",
      context: { runtime: "weapp", page: "feedback", workspace: "more" },
    });
    expect(calls[2]?.data).toEqual({ status: "resolved" });
  });

  it("uses strict Scheduled Input CRUD and run-due routes", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const command = {
      type: "expense" as const,
      amount: "88.50",
      description: "固定支出",
      next_date: "2026-09-18",
      note: null,
      enabled: true,
    };
    const { calls, requester } = requesterFor({
      [`GET ${baseUrl}/api/scheduled-inputs`]: {
        statusCode: 200,
        data: { scheduled_inputs: [scheduledPayload()] },
      },
      [`POST ${baseUrl}/api/scheduled-inputs`]: {
        statusCode: 201,
        data: { scheduled_input: scheduledPayload() },
      },
      [`PATCH ${baseUrl}/api/scheduled-inputs/schedule_1`]: {
        statusCode: 200,
        data: { scheduled_input: scheduledPayload({ enabled: false }) },
      },
      [`POST ${baseUrl}/api/scheduled-inputs/run-due`]: {
        statusCode: 200,
        data: {
          scheduled_input_run: {
            generated_count: 1,
            occurrences: [
              {
                rule_id: "schedule_1",
                occurrence_date: "2026-08-18",
                source_record_id: "source_1",
                transaction_id: "txn_1",
                action: "created",
              },
            ],
          },
        },
      },
      [`DELETE ${baseUrl}/api/scheduled-inputs/schedule_1`]: {
        statusCode: 200,
        data: { scheduled_input_deletion: { id: "schedule_1" } },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(api.scheduledInputs()).resolves.toMatchObject([{ id: "schedule_1" }]);
    await expect(api.createScheduledInput(command)).resolves.toMatchObject({ enabled: true });
    await expect(api.updateScheduledInput("schedule_1", { ...command, enabled: false })).resolves.toMatchObject({
      enabled: false,
    });
    await expect(api.runDueScheduledInputs()).resolves.toMatchObject({
      generated_count: 1,
      occurrences: [{ transaction_id: "txn_1", action: "created" }],
    });
    await expect(api.deleteScheduledInput("schedule_1")).resolves.toBe("schedule_1");

    expect(calls[1]?.data).toEqual(command);
    expect(calls[2]?.data).toEqual({ ...command, enabled: false });
    expect(calls.map((call) => [call.method, call.url])).toEqual([
      ["GET", `${baseUrl}/api/scheduled-inputs`],
      ["POST", `${baseUrl}/api/scheduled-inputs`],
      ["PATCH", `${baseUrl}/api/scheduled-inputs/schedule_1`],
      ["POST", `${baseUrl}/api/scheduled-inputs/run-due`],
      ["DELETE", `${baseUrl}/api/scheduled-inputs/schedule_1`],
    ]);
  });

  it("rejects malformed Scheduled Input execution metadata", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const { requester } = requesterFor({
      [`GET ${baseUrl}/api/scheduled-inputs`]: {
        statusCode: 200,
        data: {
          scheduled_inputs: [
            scheduledPayload({
              last_occurrence_date: "2026-08-18",
              last_source_record_id: "source_1",
              last_transaction_id: null,
              last_action: "created",
            }),
          ],
        },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(api.scheduledInputs()).rejects.toThrow("最近执行元数据不完整");
  });
});