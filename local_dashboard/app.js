(function startSpendingDashboard() {
  "use strict";

  const api = globalThis.SpendingDashboardApi;
  if (!api) {
    throw new Error("SpendingDashboardApi 未加载。");
  }

  const chartsApi = globalThis.SpendingDashboardCharts || null;
  const service = api.createStatisticsService();
  const state = {
    summary: null,
    months: [],
    trend: null,
    selectedMonth: null,
    selectedView: "categories",
    monthStatistics: null,
  };
  let chartRegistry = null;

  const elements = {
    loading: document.querySelector("[data-state='loading']"),
    error: document.querySelector("[data-state='error']"),
    errorMessage: document.querySelector("[data-error-message]"),
    errorDetails: document.querySelector("[data-error-details]"),
    reloadButton: document.querySelector("[data-action='reload']"),
    dashboard: document.querySelector("[data-state='dashboard']"),
    empty: document.querySelector("[data-state='empty']"),
    totalSpending: document.querySelector("[data-summary='total-spending']"),
    transactionCount: document.querySelector("[data-summary='transaction-count']"),
    monthCount: document.querySelector("[data-summary='month-count']"),
    monthControls: document.querySelector("[data-month-controls]"),
    monthSelect: document.querySelector("[data-month-select]"),
    monthTitle: document.querySelector("[data-month-title]"),
    monthSpending: document.querySelector("[data-month='spending']"),
    monthTransactions: document.querySelector("[data-month='transactions']"),
    tabs: Array.from(document.querySelectorAll("[data-view]")),
    listTitle: document.querySelector("[data-list-title]"),
    list: document.querySelector("[data-statistics-list]"),
    listEmpty: document.querySelector("[data-list-empty]"),
    chartCanvases: Array.from(document.querySelectorAll("[data-chart]")),
    chartErrors: Array.from(document.querySelectorAll("[data-chart-error]")),
    status: document.querySelector("[data-status]"),
  };

  function setHidden(element, hidden) {
    element.hidden = hidden;
  }

  function announce(message) {
    elements.status.textContent = message;
  }

  function renderLoading(message = "正在读取消费统计…") {
    elements.loading.textContent = message;
    setHidden(elements.loading, false);
    setHidden(elements.error, true);
    setHidden(elements.dashboard, true);
    announce(message);
  }

  function describeError(error) {
    if (!(error instanceof api.StatisticsDataError)) {
      return {
        message: "Dashboard 发生了未预期的错误。",
        details: error instanceof Error ? error.message : String(error),
      };
    }
    const detailsByCode = {
      statistics_file_unavailable:
        "统计文件可能尚未生成，或本地静态服务没有从项目根目录启动。",
      invalid_json: "统计文件内容无法解析，请重新运行后端统计生成命令。",
      unsupported_schema:
        "当前 Dashboard 不会猜测兼容未知版本，请同步更新页面或重新生成匹配版本的数据。",
      invalid_data: "统计文件缺少必要字段或包含无效值。",
      reconciliation_error:
        "后端汇总金额或交易笔数不一致，页面已停止展示，避免显示看似正常的错误结果。",
      month_not_found: "所选月份已不在最新展示数据中，请重新加载。",
    };
    return {
      message: error.message,
      details: detailsByCode[error.code] || "请检查统计文件后重新加载。",
    };
  }

  function renderError(error) {
    const description = describeError(error);
    elements.errorMessage.textContent = description.message;
    elements.errorDetails.textContent = description.details;
    setHidden(elements.loading, true);
    setHidden(elements.dashboard, true);
    setHidden(elements.error, false);
    announce(`加载失败：${description.message}`);
  }

  function renderSummary() {
    elements.totalSpending.textContent = state.summary.totalSpendingText;
    elements.transactionCount.textContent = String(state.summary.transactionCount);
    elements.monthCount.textContent = String(state.summary.monthCount);
  }

  function renderMonthOptions() {
    elements.monthSelect.replaceChildren();
    state.months.forEach((month) => {
      const option = document.createElement("option");
      option.value = month.month;
      option.textContent = month.monthLabel;
      option.selected = month.month === state.selectedMonth;
      elements.monthSelect.append(option);
    });
  }

  function createStatisticItem(name, spendingText, transactionCount, badgeText) {
    const item = document.createElement("li");
    item.className = "statistics-item";

    const identity = document.createElement("div");
    identity.className = "statistics-item__identity";

    const nameElement = document.createElement("span");
    nameElement.className = "statistics-item__name";
    nameElement.textContent = name;
    identity.append(nameElement);
    if (badgeText) {
      const badge = document.createElement("span");
      badge.className = "status-badge";
      badge.textContent = badgeText;
      identity.append(badge);
    }

    const values = document.createElement("div");
    values.className = "statistics-item__values";

    const amount = document.createElement("strong");
    amount.className = "statistics-item__amount";
    amount.textContent = spendingText;
    const count = document.createElement("span");
    count.className = "statistics-item__count";
    count.textContent = `${transactionCount} 笔`;

    values.append(amount, count);
    item.append(identity, values);
    return item;
  }

  function renderTabs() {
    elements.tabs.forEach((tab) => {
      const selected = tab.dataset.view === state.selectedView;
      tab.classList.toggle("view-tab--active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    });
  }

  function renderStatisticsList() {
    const isCategoryView = state.selectedView === "categories";
    const items = isCategoryView
      ? state.monthStatistics.categories
      : state.monthStatistics.merchants;

    elements.listTitle.textContent = isCategoryView ? "分类支出" : "商户支出";
    elements.list.replaceChildren();
    items.forEach((item) => {
      if (isCategoryView) {
        elements.list.append(
          createStatisticItem(
            item.category,
            item.spendingText,
            item.transactionCount,
            null,
          ),
        );
      } else {
        elements.list.append(
          createStatisticItem(
            item.displayName,
            item.spendingText,
            item.transactionCount,
            item.isUnclassified ? "待分类" : null,
          ),
        );
      }
    });

    const isEmpty = items.length === 0;
    elements.listEmpty.textContent = isCategoryView
      ? "这个月份没有分类统计。"
      : "这个月份没有商户统计。";
    setHidden(elements.listEmpty, !isEmpty);
    setHidden(elements.list, isEmpty);
  }

  function setChartError(key, message) {
    const error = elements.chartErrors.find(
      (element) => element.dataset.chartError === key,
    );
    if (error) {
      error.textContent = message;
      setHidden(error, false);
    }
    const canvas = elements.chartCanvases.find(
      (element) => element.dataset.chart === key,
    );
    if (canvas) {
      setHidden(canvas, true);
    }
  }

  function clearChartError(key) {
    const error = elements.chartErrors.find(
      (element) => element.dataset.chartError === key,
    );
    if (error) {
      error.textContent = "";
      setHidden(error, true);
    }
    const canvas = elements.chartCanvases.find(
      (element) => element.dataset.chart === key,
    );
    if (canvas) {
      setHidden(canvas, false);
    }
  }

  function renderCharts() {
    const chartKeys = elements.chartCanvases.map((canvas) => canvas.dataset.chart);
    if (!chartsApi) {
      chartKeys.forEach((key) => setChartError(key, "图表模块未加载，表格统计仍可正常使用。"));
      return;
    }
    if (typeof globalThis.Chart !== "function") {
      chartKeys.forEach((key) => setChartError(key, "Chart.js 未加载，表格统计仍可正常使用。"));
      return;
    }

    try {
      if (!chartRegistry) {
        chartRegistry = chartsApi.createChartRegistry(globalThis.Chart);
      }
      const configs = chartsApi.buildChartConfigs(
        state.trend,
        state.monthStatistics,
        api.formatMinorUnits,
      );
      elements.chartCanvases.forEach((canvas) => {
        const key = canvas.dataset.chart;
        clearChartError(key);
        try {
          chartRegistry.render(key, canvas, configs[key]);
        } catch (error) {
          setChartError(
            key,
            error instanceof Error ? error.message : "图表渲染失败。",
          );
        }
      });
    } catch (error) {
      chartKeys.forEach((key) =>
        setChartError(
          key,
          error instanceof Error ? error.message : "图表渲染失败。",
        ),
      );
    }
  }

  function renderMonthStatistics() {
    const month = state.monthStatistics;
    elements.monthTitle.textContent = month.monthLabel;
    elements.monthSpending.textContent = month.totalSpendingText;
    elements.monthTransactions.textContent = `${month.transactionCount} 笔净消费`;
    renderTabs();
    renderStatisticsList();
    renderCharts();
  }

  function renderDashboard() {
    renderSummary();
    const hasMonths = state.months.length > 0;
    setHidden(elements.empty, hasMonths);
    setHidden(elements.monthControls, !hasMonths);

    if (hasMonths) {
      renderMonthOptions();
      renderMonthStatistics();
    } else if (chartRegistry) {
      chartRegistry.destroyAll();
    }

    setHidden(elements.loading, true);
    setHidden(elements.error, true);
    setHidden(elements.dashboard, false);
    announce(hasMonths ? "消费统计加载完成。" : "统计文件中暂无可展示月份数据。");
  }

  async function loadMonth(month) {
    state.monthStatistics = await service.getMonthStatistics(month);
    state.selectedMonth = month;
  }

  async function loadDashboard(forceReload) {
    renderLoading(forceReload ? "正在重新读取消费统计…" : undefined);

    try {
      const snapshot = forceReload
        ? await service.reloadStatistics()
        : {
            summary: await service.getSummary(),
            months: await service.getMonths(),
            trend: await service.getTrendStatistics(),
          };
      state.summary = snapshot.summary;
      state.months = snapshot.months;
      state.trend = snapshot.trend;

      if (state.months.length === 0) {
        state.selectedMonth = null;
        state.monthStatistics = null;
      } else {
        const selectedStillExists = state.months.some(
          (month) => month.month === state.selectedMonth,
        );
        const nextMonth = selectedStillExists
          ? state.selectedMonth
          : state.months[0].month;
        await loadMonth(nextMonth);
      }
      renderDashboard();
    } catch (error) {
      renderError(error);
    }
  }

  elements.monthSelect.addEventListener("change", async (event) => {
    const month = event.target.value;
    try {
      await loadMonth(month);
      renderMonthStatistics();
      announce(`已切换到 ${state.monthStatistics.monthLabel}。`);
    } catch (error) {
      renderError(error);
    }
  });

  elements.tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.selectedView = tab.dataset.view;
      renderTabs();
      renderStatisticsList();
      announce(state.selectedView === "categories" ? "正在按分类查看。" : "正在按商户查看。");
    });
  });

  elements.reloadButton.addEventListener("click", () => loadDashboard(true));

  loadDashboard(false);
})();
