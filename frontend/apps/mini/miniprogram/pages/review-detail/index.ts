import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import { formatTransactionMoney } from "../../presentation/transaction";
import {
  createFamilySpendingApi,
  type MappingReviewPreview,
  type MappingReviewWorkspace,
} from "../../services/api";
import { markFinancialDataChanged } from "../../state/financial-refresh";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  buildReviewDetailViewModel,
  buildReviewPreviewViewModel,
  decodeReviewDescriptionQuery,
  findMerchantSuggestions,
  type ReviewPreviewViewModel,
  type ReviewRepresentativeTransaction,
} from "./model";

type DetailLoadState = "loading" | "ready" | "error";
type PreviewState = "idle" | "loading" | "ready" | "error";
type ApplyState = "idle" | "saving" | "success" | "error";

interface MerchantSuggestion {
  name: string;
  default_category: string;
}

interface ReviewDetailPageData extends Record<string, unknown>, ReviewPreviewViewModel {
  themeId: MiniThemeId;
  loadState: DetailLoadState;
  loadError: string;
  description: string;
  transactionCount: number;
  transactionCountText: string;
  totalText: string;
  latestDateText: string;
  sourceText: string;
  hasExceptions: boolean;
  exceptionText: string;
  representatives: ReviewRepresentativeTransaction[];
  merchants: MerchantSuggestion[];
  categories: string[];
  merchantInput: string;
  merchantSuggestions: MerchantSuggestion[];
  category: string;
  categoryIndex: number;
  previewState: PreviewState;
  previewError: string;
  previewToken: string;
  newMerchantConfirmed: boolean;
  applyState: ApplyState;
  applyMessage: string;
}

interface ReviewDetailPageContext {
  data: ReviewDetailPageData;
  submitting: boolean;
  setData(data: Partial<ReviewDetailPageData>): void;
}

interface ReviewDetailOptions {
  description?: string;
}

interface InputEvent {
  detail: {
    value: string;
  };
}

interface PickerEvent {
  detail: {
    value: string;
  };
}

interface MerchantTapEvent {
  currentTarget: {
    dataset: {
      name?: unknown;
      category?: unknown;
    };
  };
}

interface TransactionTapEvent {
  currentTarget: {
    dataset: {
      id?: unknown;
    };
  };
}

const initialTheme = readMiniTheme();

const emptyPreview: ReviewPreviewViewModel = {
  previewMerchant: "",
  previewCategory: "",
  isNewMerchant: false,
  merchantModeText: "",
  changesExistingMerchantDefault: false,
  previousCategoryText: "",
  descriptionTransactionCountText: "",
  descriptionAffectedText: "",
  defaultCategoryAffectedText: "",
  totalAffectedText: "",
  preservedMerchantExceptionText: "",
  preservedCategoryExceptionText: "",
};

const initialData: ReviewDetailPageData = {
  themeId: initialTheme,
  loadState: "loading",
  loadError: "",
  description: "",
  transactionCount: 0,
  transactionCountText: "",
  totalText: "",
  latestDateText: "",
  sourceText: "",
  hasExceptions: false,
  exceptionText: "",
  representatives: [],
  merchants: [],
  categories: [],
  merchantInput: "",
  merchantSuggestions: [],
  category: "",
  categoryIndex: 0,
  previewState: "idle",
  previewError: "",
  previewToken: "",
  newMerchantConfirmed: false,
  applyState: "idle",
  applyMessage: "",
  ...emptyPreview,
};

function apiClient() {
  return createFamilySpendingApi({
    baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
  });
}

function syncTheme(context: ReviewDetailPageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

function clearPreview(context: ReviewDetailPageContext): void {
  context.setData({
    previewState: "idle",
    previewError: "",
    previewToken: "",
    newMerchantConfirmed: false,
    applyState: "idle",
    applyMessage: "",
    ...emptyPreview,
  });
}

function applyMerchantInput(context: ReviewDetailPageContext, merchantInput: string): void {
  const merchantSuggestions = findMerchantSuggestions(
    merchantInput,
    context.data.merchants,
  );
  context.setData({ merchantInput, merchantSuggestions });
}

async function loadDetail(
  context: ReviewDetailPageContext,
  description: string,
): Promise<void> {
  context.setData({ loadState: "loading", loadError: "" });
  try {
    const api = apiClient();
    const [workspace, transactions] = await Promise.all([
      api.mappingReview(),
      api.transactions(),
    ]);
    const detail = buildReviewDetailViewModel(description, workspace, transactions);
    if (!detail) {
      throw new Error("这项审核已经处理或不再存在，请返回审核列表刷新。");
    }
    context.setData({
      ...detail,
      merchantInput: "",
      merchantSuggestions: [],
      category: "",
      categoryIndex: 0,
      previewState: "idle",
      previewError: "",
      previewToken: "",
      newMerchantConfirmed: false,
      applyState: "idle",
      applyMessage: "",
      ...emptyPreview,
      loadState: "ready",
    });
  } catch (error) {
    context.setData({
      loadState: "error",
      loadError: error instanceof Error ? error.message : String(error),
    });
  }
}

function previewMatchesInput(
  preview: MappingReviewPreview,
  description: string,
  merchant: string,
  category: string,
): boolean {
  return (
    preview.description === description &&
    preview.merchant === merchant &&
    preview.category === category
  );
}

Page({
  submitting: false,
  data: initialData,

  onLoad(this: ReviewDetailPageContext, options: ReviewDetailOptions) {
    applyMiniThemeChrome(initialTheme);
    const description =
      typeof options.description === "string"
        ? decodeReviewDescriptionQuery(options.description)
        : "";
    if (!description) {
      this.setData({
        loadState: "error",
        loadError: "缺少待审核 description。",
      });
      return;
    }
    this.setData({ description });
    void loadDetail(this, description);
  },

  onShow(this: ReviewDetailPageContext) {
    syncTheme(this);
  },

  onTapRefresh(this: ReviewDetailPageContext) {
    if (this.data.description) {
      void loadDetail(this, this.data.description);
    }
  },

  onMerchantInput(this: ReviewDetailPageContext, event: InputEvent) {
    clearPreview(this);
    applyMerchantInput(this, event.detail.value);
  },

  onSelectMerchant(this: ReviewDetailPageContext, event: MerchantTapEvent) {
    const name = event.currentTarget.dataset.name;
    const category = event.currentTarget.dataset.category;
    if (typeof name !== "string" || typeof category !== "string") {
      return;
    }
    clearPreview(this);
    const categoryIndex = this.data.categories.findIndex((item) => item === category);
    this.setData({
      merchantInput: name,
      merchantSuggestions: [],
      category,
      categoryIndex: categoryIndex >= 0 ? categoryIndex : 0,
    });
  },

  onCategoryChange(this: ReviewDetailPageContext, event: PickerEvent) {
    const index = Number(event.detail.value);
    if (!Number.isInteger(index) || index < 0) {
      return;
    }
    const category = this.data.categories[index];
    if (!category) {
      return;
    }
    clearPreview(this);
    this.setData({ category, categoryIndex: index });
  },

  async onTapPreview(this: ReviewDetailPageContext) {
    if (this.submitting || this.data.previewState === "loading") {
      return;
    }
    const merchant = this.data.merchantInput.trim();
    const category = this.data.category;
    if (!merchant || !category) {
      this.setData({
        previewState: "error",
        previewError: "请先填写商户并选择分类。",
      });
      return;
    }

    this.submitting = true;
    this.setData({
      previewState: "loading",
      previewError: "",
      applyState: "idle",
      applyMessage: "",
      newMerchantConfirmed: false,
    });
    try {
      const preview = await apiClient().previewMappingReview({
        description: this.data.description,
        merchant,
        category,
      });
      if (!previewMatchesInput(preview, this.data.description, merchant, category)) {
        throw new Error("Backend Preview 与当前输入不一致，请重新加载。");
      }
      this.setData({
        ...buildReviewPreviewViewModel(preview),
        previewState: "ready",
        previewToken: preview.token,
        newMerchantConfirmed: false,
      });
    } catch (error) {
      this.setData({
        previewState: "error",
        previewError: error instanceof Error ? error.message : String(error),
        previewToken: "",
      });
    } finally {
      this.submitting = false;
    }
  },

  onToggleNewMerchantConfirm(this: ReviewDetailPageContext) {
    if (this.data.previewState !== "ready" || !this.data.isNewMerchant) {
      return;
    }
    this.setData({ newMerchantConfirmed: !this.data.newMerchantConfirmed });
  },

  async onTapApply(this: ReviewDetailPageContext) {
    if (this.submitting || this.data.previewState !== "ready" || !this.data.previewToken) {
      return;
    }
    if (this.data.isNewMerchant && !this.data.newMerchantConfirmed) {
      this.setData({
        applyState: "error",
        applyMessage: "创建新商户前需要明确确认。",
      });
      return;
    }

    this.submitting = true;
    this.setData({ applyState: "saving", applyMessage: "正在应用 Mapping…" });
    try {
      const applied = await apiClient().applyMappingReview({
        description: this.data.description,
        merchant: this.data.merchantInput.trim(),
        category: this.data.category,
        preview_token: this.data.previewToken,
        confirm_new_merchant: this.data.isNewMerchant && this.data.newMerchantConfirmed,
      });
      markFinancialDataChanged();
      this.setData({
        ...buildReviewPreviewViewModel(applied),
        applyState: "success",
        applyMessage: `已应用 Mapping，${applied.total_affected_transaction_count} 笔交易已按最新规则刷新。`,
      });
    } catch (error) {
      this.setData({
        applyState: "error",
        applyMessage: error instanceof Error ? error.message : String(error),
      });
    } finally {
      this.submitting = false;
    }
  },

  onTapBackToReview() {
    wx.switchTab({ url: "/pages/review/index" });
  },

  onTapTransaction(event: TransactionTapEvent) {
    const id = event.currentTarget.dataset.id;
    if (typeof id !== "string" || id.length === 0) {
      return;
    }
    wx.navigateTo({
      url: `/pages/transaction-detail/index?id=${encodeURIComponent(id)}`,
    });
  },
});