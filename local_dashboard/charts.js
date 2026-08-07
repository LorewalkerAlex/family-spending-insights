(function attachSpendingDashboardCharts(root, factory) {
  "use strict";

  const charts = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = charts;
  }

  if (root) {
    root.SpendingDashboardCharts = charts;
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function createChartsModule() {
  "use strict";

  const COLORS = [
    "#235c43",
    "#4d8066",
    "#7aa58d",
    "#a7c7b4",
    "#8f6f3f",
    "#b58b55",
    "#d2ad77",
    "#8a5c5c",
    "#ad7777",
    "#77638e",
    "#9986ae",
    "#4f7485",
    "#7294a3",
    "#7e7557",
    "#9d9575",
    "#56665d",
    "#3f6b7a",
    "#5a6d9a",
    "#8b6b9c",
    "#a36572",
    "#b77a45",
    "#7c8f3f",
    "#4d8b89",
    "#6f5b4f",
  ];

  function alphaColor(hex, alpha) {
    const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!match) {
      return hex;
    }
    const red = Number.parseInt(match[1], 16);
    const green = Number.parseInt(match[2], 16);
    const blue = Number.parseInt(match[3], 16);
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
  }

  function requireFormatMinorUnits(formatMinorUnits) {
    if (typeof formatMinorUnits !== "function") {
      throw new TypeError("formatMinorUnits 必须是函数。");
    }
    return formatMinorUnits;
  }

  function compactMinorUnits(minorUnits) {
    const yuan = Number(minorUnits) / 100;
    if (!Number.isFinite(yuan)) {
      return "";
    }
    if (Math.abs(yuan) >= 10000) {
      return `¥${(yuan / 10000).toFixed(yuan % 10000 === 0 ? 0 : 1)}万`;
    }
    return `¥${Math.round(yuan).toLocaleString("zh-CN")}`;
  }

  function tooltipCallbacks(formatMinorUnits) {
    const format = requireFormatMinorUnits(formatMinorUnits);
    return {
      label(context) {
        const label = context.dataset && context.dataset.label
          ? `${context.dataset.label}: `
          : "";
        const parsed = context.parsed;
        const value = typeof parsed === "number" ? parsed : parsed && parsed.y;
        return `${label}${format(Number(value) || 0)}`;
      },
    };
  }

  function doughnutTooltipCallbacks(formatMinorUnits) {
    const format = requireFormatMinorUnits(formatMinorUnits);
    return {
      label(context) {
        return `${context.label}: ${format(Number(context.parsed) || 0)}`;
      },
    };
  }

  function baseCartesianOptions(formatMinorUnits) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: "index",
        intersect: false,
      },
      plugins: {
        tooltip: {
          callbacks: tooltipCallbacks(formatMinorUnits),
        },
      },
      scales: {
        x: {
          grid: { display: false },
        },
        y: {
          beginAtZero: true,
          ticks: {
            callback(value) {
              return compactMinorUnits(value);
            },
          },
        },
      },
    };
  }

  function buildCategoryDatasets(trend, options = {}) {
    return trend.categories.map((category, index) => {
      const color = COLORS[index % COLORS.length];
      const dataset = {
        label: category.category,
        data: category.spendingByMonthMinor.slice(),
        backgroundColor: options.area ? alphaColor(color, 0.22) : color,
        borderColor: color,
        borderWidth: options.area ? 2 : 1,
      };
      if (options.area) {
        dataset.fill = true;
        dataset.stack = "categories";
        dataset.tension = 0.22;
        dataset.pointRadius = 2;
        dataset.pointHoverRadius = 4;
      }
      return dataset;
    });
  }

  function buildChartConfigs(trend, monthStatistics, formatMinorUnits) {
    requireFormatMinorUnits(formatMinorUnits);
    const labels = trend.months.map((month) => month.monthLabel);
    const totals = trend.months.map((month) => month.totalSpendingMinor);

    const totalLineOptions = baseCartesianOptions(formatMinorUnits);
    totalLineOptions.plugins.legend = { display: false };

    const totalBarOptions = baseCartesianOptions(formatMinorUnits);
    totalBarOptions.plugins.legend = { display: false };

    const stackedBarOptions = baseCartesianOptions(formatMinorUnits);
    stackedBarOptions.plugins.legend = { position: "bottom" };
    stackedBarOptions.scales.x.stacked = true;
    stackedBarOptions.scales.y.stacked = true;

    const stackedAreaOptions = baseCartesianOptions(formatMinorUnits);
    stackedAreaOptions.plugins.legend = { position: "bottom" };
    stackedAreaOptions.scales.y.stacked = true;

    const groupedBarOptions = baseCartesianOptions(formatMinorUnits);
    groupedBarOptions.plugins.legend = { position: "bottom" };

    const monthCategories = monthStatistics ? monthStatistics.categories : [];

    return Object.freeze({
      totalLine: {
        type: "line",
        data: {
          labels: labels.slice(),
          datasets: [
            {
              label: "月度净消费",
              data: totals.slice(),
              borderColor: COLORS[0],
              backgroundColor: alphaColor(COLORS[0], 0.12),
              borderWidth: 2,
              pointRadius: 3,
              pointHoverRadius: 5,
              tension: 0.22,
            },
          ],
        },
        options: totalLineOptions,
      },
      totalBar: {
        type: "bar",
        data: {
          labels: labels.slice(),
          datasets: [
            {
              label: "月度净消费",
              data: totals.slice(),
              backgroundColor: COLORS[0],
              borderColor: COLORS[0],
              borderWidth: 1,
            },
          ],
        },
        options: totalBarOptions,
      },
      categoryStackedBar: {
        type: "bar",
        data: {
          labels: labels.slice(),
          datasets: buildCategoryDatasets(trend),
        },
        options: stackedBarOptions,
      },
      categoryStackedArea: {
        type: "line",
        data: {
          labels: labels.slice(),
          datasets: buildCategoryDatasets(trend, { area: true }),
        },
        options: stackedAreaOptions,
      },
      categoryGroupedBar: {
        type: "bar",
        data: {
          labels: labels.slice(),
          datasets: buildCategoryDatasets(trend),
        },
        options: groupedBarOptions,
      },
      categoryDoughnut: {
        type: "doughnut",
        data: {
          labels: monthCategories.map((category) => category.category),
          datasets: [
            {
              label: monthStatistics ? `${monthStatistics.monthLabel} 分类支出` : "分类支出",
              data: monthCategories.map((category) => category.spendingMinor),
              backgroundColor: monthCategories.map(
                (_category, index) => COLORS[index % COLORS.length],
              ),
              borderColor: "#ffffff",
              borderWidth: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: "58%",
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: doughnutTooltipCallbacks(formatMinorUnits),
            },
          },
        },
      },
    });
  }

  function createChartRegistry(ChartConstructor) {
    if (typeof ChartConstructor !== "function") {
      throw new TypeError("Chart.js 未加载，无法创建图表。");
    }

    const charts = new Map();

    function render(key, canvas, config) {
      if (!canvas) {
        throw new TypeError(`找不到图表画布：${key}`);
      }
      const existing = charts.get(key);
      if (existing && typeof existing.destroy === "function") {
        existing.destroy();
      }
      charts.delete(key);
      const chart = new ChartConstructor(canvas, config);
      charts.set(key, chart);
      return chart;
    }

    function destroyAll() {
      charts.forEach((chart) => {
        if (chart && typeof chart.destroy === "function") {
          chart.destroy();
        }
      });
      charts.clear();
    }

    return Object.freeze({ render, destroyAll });
  }

  return Object.freeze({
    COLORS,
    buildChartConfigs,
    createChartRegistry,
  });
});
