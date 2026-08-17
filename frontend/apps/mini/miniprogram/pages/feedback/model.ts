import type {
  CreateFeedbackCommand,
  FeedbackItem,
  FeedbackStatus,
} from "../../services/api";

export interface FeedbackListItem {
  id: string;
  status: FeedbackStatus;
  statusLabel: string;
  createdAtText: string;
  content: string;
  contextText: string;
  actionLabel: string;
}

export type FeedbackDraftValidation =
  | { ok: true; command: CreateFeedbackCommand }
  | { ok: false; message: string };

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function formatFeedbackCreatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function feedbackStatusLabel(status: FeedbackStatus): string {
  return status === "open" ? "待处理" : "已解决";
}

export function feedbackActionLabel(status: FeedbackStatus): string {
  return status === "open" ? "标记已解决" : "重新打开";
}

export function feedbackContextText(item: FeedbackItem): string {
  const runtime =
    item.context.runtime === "weapp"
      ? "微信小程序"
      : item.context.runtime === "desktop_web"
        ? "Desktop Web"
        : item.context.runtime || "";
  const parts = [item.context.workspace, item.context.page, runtime].filter(
    (value): value is string => Boolean(value),
  );
  return parts.length > 0 ? parts.join(" · ") : "未记录页面上下文";
}

export function buildFeedbackList(items: readonly FeedbackItem[]): FeedbackListItem[] {
  return [...items]
    .sort((left, right) => right.created_at.localeCompare(left.created_at))
    .map((item) => ({
      id: item.id,
      status: item.status,
      statusLabel: feedbackStatusLabel(item.status),
      createdAtText: formatFeedbackCreatedAt(item.created_at),
      content: item.content,
      contextText: feedbackContextText(item),
      actionLabel: feedbackActionLabel(item.status),
    }));
}

export function validateFeedbackDraft(content: string): FeedbackDraftValidation {
  const normalized = content.trim();
  if (!normalized) {
    return { ok: false, message: "请填写反馈内容。" };
  }
  return {
    ok: true,
    command: {
      content: normalized,
      context: {
        runtime: "weapp",
        page: "feedback",
        workspace: "more",
      },
    },
  };
}