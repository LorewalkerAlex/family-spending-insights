import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import {
  createFamilySpendingApi,
  type ScheduledInputRule,
  type TransactionType,
} from "../../services/api";
import { markFinancialDataChanged } from "../../state/financial-refresh";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  defaultScheduledDate,
  draftFromRule,
  scheduledSaveMessage,
  validateScheduledDraft,
} from "./model";

type LoadState = "loading" | "ready" | "error";
type MutationState = "idle" | "saving" | "success" | "error";
type EditMode = "create" | "edit";

interface ScheduledEditData extends Record<string, unknown> {
  themeId: MiniThemeId;
  loadState: LoadState;
  loadError: string;
  mode: EditMode;
  ruleId: string;
  type: TransactionType;
  amount: string;
  description: string;
  nextDate: string;
  note: string;
  enabled: boolean;
  saveState: MutationState;
  saveMessage: string;
  deleteConfirm: boolean;
  deleteState: MutationState;
  deleteMessage: string;
}

interface ScheduledEditContext {
  data: ScheduledEditData;
  busy: boolean;
  setData(data: Partial<ScheduledEditData>): void;
}

interface ScheduledEditOptions {
  id?: string;
}

interface InputEvent {
  detail: {
    value: string;
  };
}

interface DateEvent {
  detail: {
    value: string;
  };
}

interface TypeTapEvent {
  currentTarget: {
    dataset: {
      type?: unknown;
    };
  };
}

interface EnabledTapEvent {
  currentTarget: {
    dataset: {
      enabled?: unknown;
    };
  };
}

const initialTheme = readMiniTheme();

const initialData: ScheduledEditData = {
  themeId: initialTheme,
  loadState: "ready",
  loadError: "",
  mode: "create",
  ruleId: "",
  type: "expense",
  amount: "",
  description: "",
  nextDate: defaultScheduledDate(),
  note: "",
  enabled: true,
  saveState: "idle",
  saveMessage: "",
  deleteConfirm: false,
  deleteState: "idle",
  deleteMessage: "",
};

function apiClient() {
  return createFamilySpendingApi({
    baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
  });
}

function syncTheme(context: ScheduledEditContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

function decodeRuleId(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function applyRule(context: ScheduledEditContext, rule: ScheduledInputRule): void {
  const draft = draftFromRule(rule);
  context.setData({
    loadState: "ready",
    loadError: "",
    mode: "edit",
    ruleId: rule.id,
    ...draft,
  });
}

async function loadRule(context: ScheduledEditContext, ruleId: string): Promise<void> {
  context.setData({ loadState: "loading", loadError: "" });
  try {
    const rules = await apiClient().scheduledInputs();
    const rule = rules.find((item) => item.id === ruleId);
    if (!rule) {
      throw new Error("这条定期规则已经不存在，请返回列表刷新。");
    }
    applyRule(context, rule);
  } catch (error) {
    context.setData({
      loadState: "error",
      loadError: error instanceof Error ? error.message : String(error),
    });
  }
}

function isTransactionType(value: unknown): value is TransactionType {
  return value === "expense" || value === "income";
}

function resetMutationState(context: ScheduledEditContext): void {
  if (context.data.saveState !== "idle" || context.data.deleteConfirm) {
    context.setData({
      saveState: "idle",
      saveMessage: "",
      deleteConfirm: false,
      deleteState: "idle",
      deleteMessage: "",
    });
  }
}

Page({
  busy: false,
  data: initialData,

  onLoad(this: ScheduledEditContext, options: ScheduledEditOptions) {
    applyMiniThemeChrome(initialTheme);
    if (typeof options.id === "string" && options.id) {
      const ruleId = decodeRuleId(options.id);
      this.setData({ mode: "edit", ruleId });
      void loadRule(this, ruleId);
    }
  },

  onShow(this: ScheduledEditContext) {
    syncTheme(this);
  },

  onTapRetry(this: ScheduledEditContext) {
    if (this.data.ruleId) {
      void loadRule(this, this.data.ruleId);
    }
  },

  onTypeTap(this: ScheduledEditContext, event: TypeTapEvent) {
    const type = event.currentTarget.dataset.type;
    if (!isTransactionType(type)) {
      return;
    }
    resetMutationState(this);
    this.setData({ type });
  },

  onAmountInput(this: ScheduledEditContext, event: InputEvent) {
    resetMutationState(this);
    this.setData({ amount: event.detail.value });
  },

  onDescriptionInput(this: ScheduledEditContext, event: InputEvent) {
    resetMutationState(this);
    this.setData({ description: event.detail.value });
  },

  onDateChange(this: ScheduledEditContext, event: DateEvent) {
    resetMutationState(this);
    this.setData({ nextDate: event.detail.value });
  },

  onNoteInput(this: ScheduledEditContext, event: InputEvent) {
    resetMutationState(this);
    this.setData({ note: event.detail.value });
  },

  onEnabledTap(this: ScheduledEditContext, event: EnabledTapEvent) {
    const rawEnabled = event.currentTarget.dataset.enabled;
    const enabled =
      rawEnabled === true || rawEnabled === "true"
        ? true
        : rawEnabled === false || rawEnabled === "false"
          ? false
          : null;
    if (enabled === null) {
      return;
    }
    resetMutationState(this);
    this.setData({ enabled });
  },

  async onTapSave(this: ScheduledEditContext) {
    if (this.busy) {
      return;
    }
    const validation = validateScheduledDraft({
      type: this.data.type,
      amount: this.data.amount,
      description: this.data.description,
      nextDate: this.data.nextDate,
      note: this.data.note,
      enabled: this.data.enabled,
    });
    if (!validation.ok) {
      this.setData({ saveState: "error", saveMessage: validation.message });
      return;
    }

    this.busy = true;
    this.setData({ saveState: "saving", saveMessage: "正在保存规则并处理已到期项…" });
    try {
      const api = apiClient();
      const rule =
        this.data.mode === "create"
          ? await api.createScheduledInput(validation.command)
          : await api.updateScheduledInput(this.data.ruleId, validation.command);
      markFinancialDataChanged();
      applyRule(this, rule);
      this.setData({
        saveState: "success",
        saveMessage: scheduledSaveMessage(rule),
      });
    } catch (error) {
      this.setData({
        saveState: "error",
        saveMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.busy = false;
    }
  },

  onTapDelete(this: ScheduledEditContext) {
    if (this.data.mode !== "edit" || this.busy) {
      return;
    }
    this.setData({
      deleteConfirm: true,
      deleteState: "idle",
      deleteMessage: "",
    });
  },

  onCancelDelete(this: ScheduledEditContext) {
    this.setData({ deleteConfirm: false, deleteState: "idle", deleteMessage: "" });
  },

  async onConfirmDelete(this: ScheduledEditContext) {
    if (this.data.mode !== "edit" || this.busy || !this.data.ruleId) {
      return;
    }
    this.busy = true;
    this.setData({ deleteState: "saving", deleteMessage: "正在删除未来规则…" });
    try {
      await apiClient().deleteScheduledInput(this.data.ruleId);
      this.setData({
        deleteState: "success",
        deleteMessage: "规则已删除；已经生成的历史交易保持不变。",
      });
      wx.navigateBack({ delta: 1 });
    } catch (error) {
      this.setData({
        deleteState: "error",
        deleteMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.busy = false;
    }
  },

  onTapBack() {
    wx.navigateBack({ delta: 1 });
  },
});