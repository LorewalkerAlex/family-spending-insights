import { currentEnvironmentVersion, resolveApiBaseUrl } from "../../config/runtime";
import { createFamilySpendingApi, type FinancialSummaryMonth } from "../../services/api";

type ConnectionState = "connecting" | "connected" | "error";

interface HomePageData extends Record<string, unknown> {
  connectionState: ConnectionState;
  connectionLabel: string;
  environmentLabel: string;
  hasSummary: boolean;
  monthLabel: string;
  incomeText: string;
  spendingText: string;
  netText: string;
  netPositive: boolean;
  transactionText: string;
  errorMessage: string;
}

interface HomePageContext {
  setData(data: Partial<HomePageData>): void;
}

const initialData: HomePageData = {
  connectionState: "connecting",
  connectionLabel: "正在连接",
  environmentLabel: "开发环境",
  hasSummary: false,
  monthLabel: "—",
  incomeText: "—",
  spendingText: "—",
  netText: "—",
  netPositive: true,
  transactionText: "—",
  errorMessage: "",
};

function formatMoney(minor: number): string {
  const negative = minor < 0;
  const absolute = Math.abs(Math.trunc(minor));
  const yuan = Math.floor(absolute / 100);
  const cents = String(absolute % 100).padStart(2, "0");
  const grouped = String(yuan).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}¥${grouped}.${cents}`;
}

function formatMonth(value: string): string {
  const [year, month] = value.split("-");
  if (!year || !month) {
    return value;
  }
  return `${year} 年 ${Number(month)} 月`;
}

function latestVisibleMonth(months: readonly FinancialSummaryMonth[]): FinancialSummaryMonth | null {
  return months.find((item) => item.show) ?? null;
}

async function loadHome(context: HomePageContext): Promise<void> {
  context.setData({
    connectionState: "connecting",
    connectionLabel: "正在连接",
    errorMessage: "",
  });

  try {
    const envVersion = currentEnvironmentVersion();
    const api = createFamilySpendingApi({
      baseUrl: resolveApiBaseUrl(envVersion),
    });
    await api.health();
    const summary = await api.financialSummary();
    const month = latestVisibleMonth(summary.months);

    context.setData({
      connectionState: "connected",
      connectionLabel: "已连接 Canonical Backend",
      environmentLabel:
        envVersion === "develop" ? "开发环境" : envVersion === "trial" ? "体验版" : "正式版",
      hasSummary: month !== null,
      monthLabel: month ? formatMonth(month.month) : "暂无完整月份",
      incomeText: month ? formatMoney(month.total_income_minor) : "—",
      spendingText: month ? formatMoney(month.total_spending_minor) : "—",
      netText: month ? formatMoney(month.net_cash_flow_minor) : "—",
      netPositive: month ? month.net_cash_flow_minor >= 0 : true,
      transactionText: month
        ? `${month.income_transaction_count} 笔收入 · ${month.spending_transaction_count} 笔支出`
        : "等待完整自然月数据",
    });
  } catch (error) {
    context.setData({
      connectionState: "error",
      connectionLabel: "Backend 未连接",
      hasSummary: false,
      errorMessage: error instanceof Error ? error.message : String(error),
    });
  }
}

Page({
  data: initialData,

  onLoad(this: HomePageContext) {
    void loadHome(this);
  },

  onPullDownRefresh(this: HomePageContext) {
    void loadHome(this).finally(() => wx.stopPullDownRefresh());
  },

  onTapRefresh(this: HomePageContext) {
    void loadHome(this);
  },
});
