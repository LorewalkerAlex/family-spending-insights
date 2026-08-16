import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import { createFamilySpendingApi } from "../../services/api";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  buildTransactionDetailViewModel,
  type TransactionDetailViewModel,
} from "./model";

type DetailLoadState = "loading" | "ready" | "error";

interface TransactionDetailPageData extends Record<string, unknown>, TransactionDetailViewModel {
  themeId: MiniThemeId;
  loadState: DetailLoadState;
  errorMessage: string;
}

interface TransactionDetailPageContext {
  transactionId: string;
  setData(data: Partial<TransactionDetailPageData>): void;
}

interface TransactionDetailPageOptions {
  id?: string;
}

const initialTheme = readMiniTheme();

const initialData: TransactionDetailPageData = {
  themeId: initialTheme,
  loadState: "loading",
  errorMessage: "",
  id: "",
  typeLabel: "",
  amountText: "",
  amountTone: "negative",
  dateText: "",
  name: "",
  merchantText: "",
  categoryText: "",
  categorySourceText: "",
  rawDescription: "",
  sourceText: "",
  hasNote: false,
  noteText: "",
  isUnclassified: false,
  hasReviewSignals: false,
  reviewSignals: [],
};

function syncTheme(context: TransactionDetailPageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

async function loadTransaction(context: TransactionDetailPageContext): Promise<void> {
  if (!context.transactionId) {
    context.setData({ loadState: "error", errorMessage: "缺少交易 ID" });
    return;
  }

  context.setData({ loadState: "loading", errorMessage: "" });
  try {
    const api = createFamilySpendingApi({
      baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
    });
    const transaction = await api.transaction(context.transactionId);
    context.setData({
      ...buildTransactionDetailViewModel(transaction),
      loadState: "ready",
    });
  } catch (error) {
    context.setData({
      loadState: "error",
      errorMessage: error instanceof Error ? error.message : String(error),
    });
  }
}

Page({
  transactionId: "",
  data: initialData,

  onLoad(this: TransactionDetailPageContext, options: TransactionDetailPageOptions) {
    applyMiniThemeChrome(initialTheme);
    const id = typeof options.id === "string" ? options.id.trim() : "";
    this.transactionId = id;
    void loadTransaction(this);
  },

  onShow(this: TransactionDetailPageContext) {
    syncTheme(this);
  },

  onTapRefresh(this: TransactionDetailPageContext) {
    void loadTransaction(this);
  },
});
