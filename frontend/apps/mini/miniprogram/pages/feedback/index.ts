import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import {
  createFamilySpendingApi,
  type FeedbackItem,
  type FeedbackStatus,
} from "../../services/api";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  buildFeedbackList,
  validateFeedbackDraft,
  type FeedbackListItem,
} from "./model";

type LoadState = "loading" | "ready" | "error";
type SubmitState = "idle" | "saving" | "success" | "error";

interface FeedbackPageData extends Record<string, unknown> {
  themeId: MiniThemeId;
  loadState: LoadState;
  errorMessage: string;
  items: FeedbackListItem[];
  content: string;
  submitState: SubmitState;
  submitMessage: string;
  pendingId: string;
}

interface FeedbackPageContext {
  data: FeedbackPageData;
  feedback: FeedbackItem[];
  submitting: boolean;
  setData(data: Partial<FeedbackPageData>): void;
}

interface InputEvent {
  detail: {
    value: string;
  };
}

interface StatusTapEvent {
  currentTarget: {
    dataset: {
      id?: unknown;
      status?: unknown;
    };
  };
}

const initialTheme = readMiniTheme();

const initialData: FeedbackPageData = {
  themeId: initialTheme,
  loadState: "loading",
  errorMessage: "",
  items: [],
  content: "",
  submitState: "idle",
  submitMessage: "",
  pendingId: "",
};

function apiClient() {
  return createFamilySpendingApi({
    baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
  });
}

function syncTheme(context: FeedbackPageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

function applyItems(context: FeedbackPageContext): void {
  context.setData({ items: buildFeedbackList(context.feedback) });
}

async function loadFeedback(context: FeedbackPageContext): Promise<void> {
  context.setData({ loadState: "loading", errorMessage: "" });
  try {
    context.feedback = await apiClient().feedback();
    context.setData({
      loadState: "ready",
      items: buildFeedbackList(context.feedback),
    });
  } catch (error) {
    context.setData({
      loadState: "error",
      errorMessage: error instanceof Error ? error.message : String(error),
    });
  }
}

function isFeedbackStatus(value: unknown): value is FeedbackStatus {
  return value === "open" || value === "resolved";
}

Page({
  feedback: [] as FeedbackItem[],
  submitting: false,
  data: initialData,

  onLoad(this: FeedbackPageContext) {
    applyMiniThemeChrome(initialTheme);
    void loadFeedback(this);
  },

  onShow(this: FeedbackPageContext) {
    syncTheme(this);
  },

  onPullDownRefresh(this: FeedbackPageContext) {
    void loadFeedback(this).finally(() => wx.stopPullDownRefresh());
  },

  onTapRefresh(this: FeedbackPageContext) {
    void loadFeedback(this);
  },

  onContentInput(this: FeedbackPageContext, event: InputEvent) {
    this.setData({
      content: event.detail.value,
      submitState: "idle",
      submitMessage: "",
    });
  },

  async onTapSubmit(this: FeedbackPageContext) {
    if (this.submitting) {
      return;
    }
    const validation = validateFeedbackDraft(this.data.content);
    if (!validation.ok) {
      this.setData({ submitState: "error", submitMessage: validation.message });
      return;
    }

    this.submitting = true;
    this.setData({ submitState: "saving", submitMessage: "正在保存反馈…" });
    try {
      const created = await apiClient().createFeedback(validation.command);
      this.feedback = [created, ...this.feedback.filter((item) => item.id !== created.id)];
      this.setData({
        content: "",
        submitState: "success",
        submitMessage: "反馈已保存到家庭账本的本地产品反馈中。",
      });
      applyItems(this);
    } catch (error) {
      this.setData({
        submitState: "error",
        submitMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.submitting = false;
    }
  },

  async onToggleStatus(this: FeedbackPageContext, event: StatusTapEvent) {
    const id = event.currentTarget.dataset.id;
    const status = event.currentTarget.dataset.status;
    if (typeof id !== "string" || !isFeedbackStatus(status) || this.data.pendingId) {
      return;
    }
    const nextStatus: FeedbackStatus = status === "open" ? "resolved" : "open";
    this.setData({ pendingId: id, errorMessage: "" });
    try {
      const updated = await apiClient().updateFeedbackStatus(id, nextStatus);
      this.feedback = this.feedback.map((item) => (item.id === id ? updated : item));
      applyItems(this);
    } catch (error) {
      this.setData({
        errorMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.setData({ pendingId: "" });
    }
  },
});