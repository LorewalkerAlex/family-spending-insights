import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import { createFamilySpendingApi } from "../../services/api";
import {
  currentFinancialDataVersion,
  hasFinancialDataChanged,
} from "../../state/financial-refresh";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  buildReviewListViewModel,
  type ReviewListItemViewModel,
} from "./model";

type ReviewLoadState = "loading" | "ready" | "error";

interface ReviewPageData extends Record<string, unknown> {
  themeId: MiniThemeId;
  loadState: ReviewLoadState;
  reviewCount: number;
  reviewCountText: string;
  isEmpty: boolean;
  items: ReviewListItemViewModel[];
  errorMessage: string;
}

interface ReviewPageContext {
  data: ReviewPageData;
  hasLoaded: boolean;
  financialDataVersion: number;
  setData(data: Partial<ReviewPageData>): void;
}

interface ReviewTapEvent {
  currentTarget: {
    dataset: {
      description?: unknown;
    };
  };
}

const initialTheme = readMiniTheme();

const initialData: ReviewPageData = {
  themeId: initialTheme,
  loadState: "loading",
  reviewCount: 0,
  reviewCountText: "正在读取",
  isEmpty: false,
  items: [],
  errorMessage: "",
};

function syncTheme(context: ReviewPageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

async function loadReview(context: ReviewPageContext): Promise<void> {
  context.setData({ loadState: "loading", errorMessage: "" });
  try {
    const api = createFamilySpendingApi({
      baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
    });
    const workspace = await api.mappingReview();
    context.financialDataVersion = currentFinancialDataVersion();
    context.setData({ ...buildReviewListViewModel(workspace), loadState: "ready" });
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
  hasLoaded: false,
  financialDataVersion: currentFinancialDataVersion(),
  data: initialData,

  onLoad(this: ReviewPageContext) {
    applyMiniThemeChrome(initialTheme);
    void loadReview(this);
  },

  onShow(this: ReviewPageContext) {
    syncTheme(this);
    if (
      this.hasLoaded &&
      hasFinancialDataChanged(this.financialDataVersion)
    ) {
      void loadReview(this);
    }
  },

  onPullDownRefresh(this: ReviewPageContext) {
    void loadReview(this).finally(() => wx.stopPullDownRefresh());
  },

  onTapRefresh(this: ReviewPageContext) {
    void loadReview(this);
  },

  onTapReviewItem(event: ReviewTapEvent) {
    const description = event.currentTarget.dataset.description;
    if (typeof description !== "string" || description.length === 0) {
      return;
    }
    wx.navigateTo({
      url: `/pages/review-detail/index?description=${encodeURIComponent(description)}`,
    });
  },
});