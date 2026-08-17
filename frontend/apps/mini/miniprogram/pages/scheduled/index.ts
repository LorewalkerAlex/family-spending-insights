import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import {
  createFamilySpendingApi,
  type ScheduledInputRule,
} from "../../services/api";
import { markFinancialDataChanged } from "../../state/financial-refresh";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  buildScheduledList,
  buildScheduledSummary,
  scheduledRunMessage,
  type ScheduledListItem,
} from "./model";

type LoadState = "loading" | "ready" | "error";
type RunState = "idle" | "running" | "success" | "error";

interface ScheduledPageData extends Record<string, unknown> {
  themeId: MiniThemeId;
  loadState: LoadState;
  errorMessage: string;
  items: ScheduledListItem[];
  totalCount: number;
  enabledCount: number;
  pausedCount: number;
  runState: RunState;
  runMessage: string;
}

interface ScheduledPageContext {
  data: ScheduledPageData;
  rules: ScheduledInputRule[];
  hasLoaded: boolean;
  running: boolean;
  setData(data: Partial<ScheduledPageData>): void;
}

interface RuleTapEvent {
  currentTarget: {
    dataset: {
      id?: unknown;
    };
  };
}

const initialTheme = readMiniTheme();

const initialData: ScheduledPageData = {
  themeId: initialTheme,
  loadState: "loading",
  errorMessage: "",
  items: [],
  totalCount: 0,
  enabledCount: 0,
  pausedCount: 0,
  runState: "idle",
  runMessage: "",
};

function apiClient() {
  return createFamilySpendingApi({
    baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
  });
}

function syncTheme(context: ScheduledPageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

function applyRules(context: ScheduledPageContext): void {
  const summary = buildScheduledSummary(context.rules);
  context.setData({
    items: buildScheduledList(context.rules),
    ...summary,
  });
}

async function loadRules(context: ScheduledPageContext): Promise<void> {
  context.setData({ loadState: "loading", errorMessage: "" });
  try {
    context.rules = await apiClient().scheduledInputs();
    const summary = buildScheduledSummary(context.rules);
    context.setData({
      loadState: "ready",
      items: buildScheduledList(context.rules),
      ...summary,
    });
  } catch (error) {
    context.setData({
      loadState: "error",
      errorMessage: error instanceof Error ? error.message : String(error),
    });
  } finally {
    context.hasLoaded = true;
  }
}

Page({
  rules: [] as ScheduledInputRule[],
  hasLoaded: false,
  running: false,
  data: initialData,

  onLoad(this: ScheduledPageContext) {
    applyMiniThemeChrome(initialTheme);
    void loadRules(this);
  },

  onShow(this: ScheduledPageContext) {
    syncTheme(this);
    if (this.hasLoaded) {
      void loadRules(this);
    }
  },

  onPullDownRefresh(this: ScheduledPageContext) {
    void loadRules(this).finally(() => wx.stopPullDownRefresh());
  },

  onTapRefresh(this: ScheduledPageContext) {
    void loadRules(this);
  },

  onTapCreate() {
    wx.navigateTo({ url: "/pages/scheduled-edit/index" });
  },

  onTapRule(event: RuleTapEvent) {
    const id = event.currentTarget.dataset.id;
    if (typeof id !== "string" || !id) {
      return;
    }
    wx.navigateTo({ url: `/pages/scheduled-edit/index?id=${encodeURIComponent(id)}` });
  },

  async onRunDue(this: ScheduledPageContext) {
    if (this.running) {
      return;
    }
    this.running = true;
    this.setData({ runState: "running", runMessage: "正在执行已到期规则…" });
    try {
      const result = await apiClient().runDueScheduledInputs();
      if (result.generated_count > 0) {
        markFinancialDataChanged();
      }
      this.setData({ runState: "success", runMessage: scheduledRunMessage(result) });
      this.rules = await apiClient().scheduledInputs();
      applyRules(this);
    } catch (error) {
      this.setData({
        runState: "error",
        runMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.running = false;
    }
  },
});