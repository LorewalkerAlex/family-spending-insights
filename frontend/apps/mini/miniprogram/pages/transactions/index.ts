import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import { createFamilySpendingApi, type Transaction } from "../../services/api";
import {
  applyMiniThemeChrome,
  readMiniTheme,
  type MiniThemeId,
} from "../../theme/index";
import {
  buildTransactionsViewModel,
  type TransactionDayGroup,
  type TransactionFilter,
  type TransactionMonthOption,
} from "./model";

type TransactionsLoadState = "loading" | "ready" | "error";

interface TransactionsPageData extends Record<string, unknown> {
  themeId: MiniThemeId;
  loadState: TransactionsLoadState;
  monthOptions: TransactionMonthOption[];
  selectedMonth: string;
  selectedMonthIndex: number;
  selectedMonthLabel: string;
  filter: TransactionFilter;
  groups: TransactionDayGroup[];
  transactionCount: number;
  isCompletelyEmpty: boolean;
  isFilteredEmpty: boolean;
  errorMessage: string;
}

interface TransactionsPageContext {
  data: TransactionsPageData;
  transactions: Transaction[];
  setData(data: Partial<TransactionsPageData>): void;
}

interface PickerChangeEvent {
  detail: {
    value: string;
  };
}

interface FilterTapEvent {
  currentTarget: {
    dataset: {
      filter?: unknown;
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

const initialData: TransactionsPageData = {
  themeId: initialTheme,
  loadState: "loading",
  monthOptions: [],
  selectedMonth: "",
  selectedMonthIndex: 0,
  selectedMonthLabel: "读取中",
  filter: "all",
  groups: [],
  transactionCount: 0,
  isCompletelyEmpty: false,
  isFilteredEmpty: false,
  errorMessage: "",
};

function syncTheme(context: TransactionsPageContext): void {
  const themeId = readMiniTheme();
  context.setData({ themeId });
  applyMiniThemeChrome(themeId);
}

function applyView(
  context: TransactionsPageContext,
  requestedMonth: string | null,
  filter: TransactionFilter,
): void {
  context.setData({ ...buildTransactionsViewModel(context.transactions, requestedMonth, filter) });
}

async function loadTransactions(context: TransactionsPageContext): Promise<void> {
  context.setData({ loadState: "loading", errorMessage: "" });
  try {
    const api = createFamilySpendingApi({
      baseUrl: resolveApiBaseUrl(currentEnvironmentVersion()),
    });
    context.transactions = await api.transactions();
    const data = context.data;
    const view = buildTransactionsViewModel(
      context.transactions,
      data.selectedMonth || null,
      data.filter,
    );
    context.setData({ ...view, loadState: "ready" });
  } catch (error) {
    context.setData({
      loadState: "error",
      errorMessage: error instanceof Error ? error.message : String(error),
    });
  }
}

function isTransactionFilter(value: unknown): value is TransactionFilter {
  return value === "all" || value === "expense" || value === "income";
}

Page({
  transactions: [] as Transaction[],
  data: initialData,

  onLoad(this: TransactionsPageContext) {
    applyMiniThemeChrome(initialTheme);
    void loadTransactions(this);
  },

  onShow(this: TransactionsPageContext) {
    syncTheme(this);
  },

  onPullDownRefresh(this: TransactionsPageContext) {
    void loadTransactions(this).finally(() => wx.stopPullDownRefresh());
  },

  onTapRefresh(this: TransactionsPageContext) {
    void loadTransactions(this);
  },

  onMonthChange(this: TransactionsPageContext, event: PickerChangeEvent) {
    const index = Number(event.detail.value);
    if (!Number.isInteger(index) || index < 0) {
      return;
    }
    const data = this.data;
    const month = data.monthOptions[index]?.value;
    if (!month) {
      return;
    }
    applyView(this, month, data.filter);
  },

  onFilterTap(this: TransactionsPageContext, event: FilterTapEvent) {
    const filter = event.currentTarget.dataset.filter;
    if (!isTransactionFilter(filter)) {
      return;
    }
    const data = this.data;
    applyView(this, data.selectedMonth, filter);
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
