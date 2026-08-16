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
import { buildHomeViewModel, type HomeTransactionItem } from "./model";

type HomeLoadState = "loading" | "ready" | "error";

interface HomePageData extends Record<string, unknown> {
  themeId: MiniThemeId;
  loadState: HomeLoadState;
  hasSummary: boolean;
  monthLabel: string;
  spendingText: string;
  incomeText: string;
  netText: string;
  netTone: "positive" | "negative" | "neutral";
  reviewCount: number;
  reviewTitle: string;
  reviewBody: string;
  recentTransactions: HomeTransactionItem[];
  isCompletelyEmpty: boolean;
  errorMessage: string;
}

interface HomePageContext {
  hasLoaded: boolean;
  financialDataVersion: number;
  setData(data: Partial<HomePageData>): void;
}

interface TransactionTapEvent {
  currentTarget: {
    dataset: {
      id?: unknown;
    };
  };
}

const initialTheme = readMiniTheme();

const initialData: HomePageData = {
  themeId: initialTheme,
  loadState: "loading",
  hasSummary: false,
  monthLabel: "本月账目",
  spendingText: "—",
  incomeText: "—",
  netText: "—",
  netTone: "neutral",
  reviewCount: 0,
  reviewTitle: "正在读取",
  reviewBody: "",
  recentTransactions: [],
  isCompletelyEmpty: false,
  errorMessage: "",
};

function syncTheme(context: HomePageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

/** Load the three authoritative Home queries together so the page reflects one coherent refresh. */
async function loadHome(context: HomePageContext): Promise<void> {
  context.setData({ loadState: "loading", errorMessage: "" });

  try {
    const api = createFamilySpendingApi({
      baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
    });
    const [summary, transactions, review] = await Promise.all([
      api.financialSummary(),
      api.transactions(),
      api.mappingReview(),
    ]);
    const viewModel = buildHomeViewModel(summary, transactions, review);
    context.financialDataVersion = currentFinancialDataVersion();
    context.setData({ ...viewModel, loadState: "ready" });
  } catch (error) {
    context.setData({
      loadState: "error",
      errorMessage: error instanceof Error ? error.message : String(error),
    });
  } finally {
    context.hasLoaded = true;
  }
}

function openTransactionDetail(id: unknown): void {
  if (typeof id !== "string" || id.length === 0) {
    return;
  }
  wx.navigateTo({
    url: `/pages/transaction-detail/index?id=${encodeURIComponent(id)}`,
  });
}

Page({
  hasLoaded: false,
  financialDataVersion: currentFinancialDataVersion(),
  data: initialData,

  onLoad(this: HomePageContext) {
    applyMiniThemeChrome(initialTheme);
    void loadHome(this);
  },

  onShow(this: HomePageContext) {
    syncTheme(this);
    if (
      this.hasLoaded &&
      hasFinancialDataChanged(this.financialDataVersion)
    ) {
      void loadHome(this);
    }
  },

  onPullDownRefresh(this: HomePageContext) {
    void loadHome(this).finally(() => wx.stopPullDownRefresh());
  },

  onTapRefresh(this: HomePageContext) {
    void loadHome(this);
  },

  onTapTransactions() {
    wx.switchTab({ url: "/pages/transactions/index" });
  },

  onTapTransaction(event: TransactionTapEvent) {
    openTransactionDetail(event.currentTarget.dataset.id);
  },

  onTapReview() {
    wx.switchTab({ url: "/pages/review/index" });
  },

  onTapTheme() {
    wx.switchTab({ url: "/pages/more/index" });
  },
});
