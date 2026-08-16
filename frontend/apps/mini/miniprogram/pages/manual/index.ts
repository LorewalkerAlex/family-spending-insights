import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import { transactionDisplayName } from "../../presentation/transaction";
import {
  createFamilySpendingApi,
  type TransactionType,
} from "../../services/api";
import { markFinancialDataChanged } from "../../state/financial-refresh";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  buildDescriptionAssist,
  manualInputActionLabel,
  mergeManualDescription,
  todayIsoDate,
  validateManualDraft,
} from "./model";

type DescriptionAssistState = "loading" | "ready" | "error";
type ManualSubmitState = "idle" | "saving" | "success" | "error";

interface ManualPageData extends Record<string, unknown> {
  themeId: MiniThemeId;
  type: TransactionType;
  date: string;
  amount: string;
  description: string;
  note: string;
  descriptions: string[];
  suggestions: string[];
  normalizedDuplicate: string;
  hasExactExisting: boolean;
  hasNormalizedDuplicate: boolean;
  currentDescriptionConfirmed: boolean;
  descriptionAssistState: DescriptionAssistState;
  descriptionAssistMessage: string;
  submitState: ManualSubmitState;
  submitMessage: string;
  successTransactionId: string;
  successTransactionName: string;
}

interface ManualPageContext {
  data: ManualPageData;
  submitting: boolean;
  setData(data: Partial<ManualPageData>): void;
}

interface TextInputEvent {
  detail: {
    value: string;
  };
}

interface DateChangeEvent {
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

interface SuggestionTapEvent {
  currentTarget: {
    dataset: {
      description?: unknown;
    };
  };
}

const initialTheme = readMiniTheme();

const initialData: ManualPageData = {
  themeId: initialTheme,
  type: "expense",
  date: todayIsoDate(),
  amount: "",
  description: "",
  note: "",
  descriptions: [],
  suggestions: [],
  normalizedDuplicate: "",
  hasExactExisting: false,
  hasNormalizedDuplicate: false,
  currentDescriptionConfirmed: false,
  descriptionAssistState: "loading",
  descriptionAssistMessage: "正在读取历史描述…",
  submitState: "idle",
  submitMessage: "",
  successTransactionId: "",
  successTransactionName: "",
};

function apiClient() {
  return createFamilySpendingApi({
    baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
  });
}

function syncTheme(context: ManualPageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

function clearSubmitOutcome(context: ManualPageContext): void {
  if (context.data.submitState === "idle" || context.data.submitState === "saving") {
    return;
  }
  context.setData({
    submitState: "idle",
    submitMessage: "",
    successTransactionId: "",
    successTransactionName: "",
  });
}

function applyDescriptionAssist(
  context: ManualPageContext,
  description: string,
  confirmedNewDescription = "",
): void {
  const assist = buildDescriptionAssist(description, context.data.descriptions);
  context.setData({
    description,
    suggestions: assist.suggestions,
    normalizedDuplicate: assist.normalizedDuplicate,
    hasExactExisting: assist.hasExactExisting,
    hasNormalizedDuplicate: assist.hasNormalizedDuplicate,
    currentDescriptionConfirmed:
      assist.hasNormalizedDuplicate && confirmedNewDescription === description.trim(),
  });
}

async function loadDescriptions(context: ManualPageContext): Promise<void> {
  context.setData({
    descriptionAssistState: "loading",
    descriptionAssistMessage: "正在读取历史描述…",
  });
  try {
    const descriptions = await apiClient().manualDescriptions();
    const assist = buildDescriptionAssist(context.data.description, descriptions);
    context.setData({
      descriptions,
      suggestions: assist.suggestions,
      normalizedDuplicate: assist.normalizedDuplicate,
      hasExactExisting: assist.hasExactExisting,
      hasNormalizedDuplicate: assist.hasNormalizedDuplicate,
      currentDescriptionConfirmed: false,
      descriptionAssistState: "ready",
      descriptionAssistMessage: "",
    });
  } catch (error) {
    context.setData({
      descriptionAssistState: "error",
      descriptionAssistMessage:
        error instanceof Error
          ? `历史描述暂时不可用：${error.message}`
          : "历史描述暂时不可用；仍可直接录入。",
    });
  }
}

function isTransactionType(value: unknown): value is TransactionType {
  return value === "expense" || value === "income";
}

Page({
  submitting: false,
  data: initialData,

  onLoad(this: ManualPageContext) {
    applyMiniThemeChrome(initialTheme);
    void loadDescriptions(this);
  },

  onShow(this: ManualPageContext) {
    syncTheme(this);
  },

  onTypeTap(this: ManualPageContext, event: TypeTapEvent) {
    const type = event.currentTarget.dataset.type;
    if (!isTransactionType(type) || type === this.data.type) {
      return;
    }
    clearSubmitOutcome(this);
    this.setData({ type });
  },

  onDateChange(this: ManualPageContext, event: DateChangeEvent) {
    clearSubmitOutcome(this);
    this.setData({ date: event.detail.value });
  },

  onAmountInput(this: ManualPageContext, event: TextInputEvent) {
    clearSubmitOutcome(this);
    this.setData({ amount: event.detail.value });
  },

  onDescriptionInput(this: ManualPageContext, event: TextInputEvent) {
    clearSubmitOutcome(this);
    applyDescriptionAssist(this, event.detail.value);
  },

  onNoteInput(this: ManualPageContext, event: TextInputEvent) {
    clearSubmitOutcome(this);
    this.setData({ note: event.detail.value });
  },

  onUseSuggestion(this: ManualPageContext, event: SuggestionTapEvent) {
    const description = event.currentTarget.dataset.description;
    if (typeof description !== "string" || !description) {
      return;
    }
    clearSubmitOutcome(this);
    applyDescriptionAssist(this, description);
  },

  onConfirmCurrentDescription(this: ManualPageContext) {
    const description = this.data.description.trim();
    if (!description || !this.data.hasNormalizedDuplicate) {
      return;
    }
    clearSubmitOutcome(this);
    applyDescriptionAssist(this, this.data.description, description);
  },

  async onTapSubmit(this: ManualPageContext) {
    if (this.submitting) {
      return;
    }

    const validation = validateManualDraft(
      {
        type: this.data.type,
        date: this.data.date,
        amount: this.data.amount,
        description: this.data.description,
        note: this.data.note,
      },
      this.data.descriptions,
      this.data.currentDescriptionConfirmed ? this.data.description.trim() : "",
    );
    if (!validation.ok) {
      this.setData({
        submitState: "error",
        submitMessage: validation.message,
        successTransactionId: "",
        successTransactionName: "",
      });
      return;
    }

    this.submitting = true;
    this.setData({
      submitState: "saving",
      submitMessage: "正在录入、对账并刷新家庭账本…",
      successTransactionId: "",
      successTransactionName: "",
    });

    try {
      const result = await apiClient().createManualInput(validation.command);
      markFinancialDataChanged();
      const nextDescriptions = mergeManualDescription(
        this.data.descriptions,
        validation.command.description,
      );
      this.setData({
        descriptions: nextDescriptions,
        amount: "",
        description: "",
        note: "",
        suggestions: [],
        normalizedDuplicate: "",
        hasExactExisting: false,
        hasNormalizedDuplicate: false,
        currentDescriptionConfirmed: false,
        submitState: "success",
        submitMessage: `${manualInputActionLabel(result.action)}。后端已完成对账与刷新。`,
        successTransactionId: result.transaction.id,
        successTransactionName: transactionDisplayName(result.transaction),
      });
    } catch (error) {
      this.setData({
        submitState: "error",
        submitMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.submitting = false;
    }
  },

  onTapSuccessTransaction(this: ManualPageContext) {
    const transactionId = this.data.successTransactionId;
    if (!transactionId) {
      return;
    }
    wx.navigateTo({
      url: `/pages/transaction-detail/index?id=${encodeURIComponent(transactionId)}`,
    });
  },

  onTapContinue(this: ManualPageContext) {
    this.setData({
      submitState: "idle",
      submitMessage: "",
      successTransactionId: "",
      successTransactionName: "",
    });
  },
});
