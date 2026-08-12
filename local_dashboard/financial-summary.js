(function startFinancialSummary() {
  "use strict";

  const api = globalThis.FamilySpendingFinancialApi;
  const root = document.querySelector("[data-financial-summary]");
  if (!api || !root) {
    return;
  }

  const service = api.createFinancialSummaryService();
  const elements = {
    status: root.querySelector("[data-financial-status]"),
    income: root.querySelector("[data-financial-summary='income']"),
    spending: root.querySelector("[data-financial-summary='spending']"),
    cashFlow: root.querySelector("[data-financial-summary='cash-flow']"),
    monthCount: root.querySelector("[data-financial-summary='month-count']"),
    monthLabel: root.querySelector("[data-financial-month-label]"),
    monthIncome: root.querySelector("[data-financial-month='income']"),
    monthSpending: root.querySelector("[data-financial-month='spending']"),
    monthCashFlow: root.querySelector("[data-financial-month='cash-flow']"),
    monthSelect: root.querySelector("[data-financial-month-select]"),
    reload: document.querySelector("[data-action='reload']"),
  };

  let payload = null;

  function selectedMonthName() {
    const selected = elements.monthSelect ? elements.monthSelect.value : "";
    return /^\d{4}-\d{2}$/.test(selected) ? selected : null;
  }

  function renderMonthOptions() {
    if (!elements.monthSelect || !payload) {
      return;
    }
    const previous = elements.monthSelect.value;
    const shownMonths = payload.months.filter((month) => month.show);
    elements.monthSelect.replaceChildren();
    shownMonths.forEach((month) => {
      const option = document.createElement("option");
      option.value = month.month;
      option.textContent = month.month;
      elements.monthSelect.append(option);
    });
    if (shownMonths.some((month) => month.month === previous)) {
      elements.monthSelect.value = previous;
    } else if (shownMonths.length > 0) {
      elements.monthSelect.value = shownMonths[0].month;
    }
    elements.monthSelect.disabled = shownMonths.length === 0;
  }

  function renderSelectedMonth() {
    if (!payload) {
      return;
    }
    const monthName = selectedMonthName();
    const month = payload.months.find((item) => item.month === monthName) || null;
    elements.monthLabel.textContent = month ? `${month.month} 月度现金流` : "所选月份暂无财务数据";
    elements.monthIncome.textContent = api.formatMinorUnits(month ? month.totalIncomeMinor : 0);
    elements.monthSpending.textContent = api.formatMinorUnits(month ? month.totalSpendingMinor : 0);
    elements.monthCashFlow.textContent = api.formatMinorUnits(month ? month.netCashFlowMinor : 0);
  }

  function render() {
    const summary = payload.summary.shownData;
    elements.income.textContent = api.formatMinorUnits(summary.totalIncomeMinor);
    elements.spending.textContent = api.formatMinorUnits(summary.totalSpendingMinor);
    elements.cashFlow.textContent = api.formatMinorUnits(summary.netCashFlowMinor);
    elements.monthCount.textContent = String(summary.monthCount);
    elements.status.textContent =
      "展示月份沿用信用卡消费覆盖策略；这里只表示消费侧覆盖完整，不宣称收入来源已经穷尽。";
    renderMonthOptions();
    renderSelectedMonth();
  }

  async function load() {
    elements.status.textContent = "正在读取家庭财务摘要…";
    try {
      payload = await service.load();
      render();
    } catch (error) {
      elements.status.textContent =
        error instanceof api.FinancialSummaryDataError
          ? `家庭财务摘要加载失败：${error.message}`
          : `家庭财务摘要加载失败：${error instanceof Error ? error.message : String(error)}`;
    }
  }

  if (elements.monthSelect) {
    elements.monthSelect.addEventListener("change", renderSelectedMonth);
  }
  if (elements.reload) {
    elements.reload.addEventListener("click", () => void load());
  }
  void load();
})();
