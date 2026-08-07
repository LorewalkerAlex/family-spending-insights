"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { buildChartConfigs, createChartRegistry } = require("./charts.js");
const { formatMinorUnits } = require("./api.js");

function makeTrend() {
  return {
    months: [
      { month: "2026-05", monthLabel: "2026 年 5 月", totalSpendingMinor: 50000 },
      { month: "2026-06", monthLabel: "2026 年 6 月", totalSpendingMinor: 70000 },
    ],
    categories: [
      {
        category: "餐饮美食",
        totalSpendingMinor: 80000,
        spendingByMonthMinor: [30000, 50000],
      },
      {
        category: "日常采购",
        totalSpendingMinor: 40000,
        spendingByMonthMinor: [20000, 20000],
      },
    ],
  };
}

function makeMonth() {
  return {
    month: "2026-06",
    monthLabel: "2026 年 6 月",
    categories: [
      { category: "餐饮美食", spendingMinor: 50000 },
      { category: "日常采购", spendingMinor: 20000 },
    ],
  };
}

test("builds all POC chart configurations from one view model", () => {
  const configs = buildChartConfigs(makeTrend(), makeMonth(), formatMinorUnits);
  assert.deepEqual(Object.keys(configs), [
    "totalLine",
    "totalBar",
    "categoryStackedBar",
    "categoryStackedArea",
    "categoryGroupedBar",
    "categoryDoughnut",
  ]);
  assert.deepEqual(configs.totalLine.data.datasets[0].data, [50000, 70000]);
  assert.deepEqual(configs.categoryDoughnut.data.datasets[0].data, [50000, 20000]);
});

test("stacked bar and stacked area use Chart.js stacking options", () => {
  const configs = buildChartConfigs(makeTrend(), makeMonth(), formatMinorUnits);
  assert.equal(configs.categoryStackedBar.options.scales.x.stacked, true);
  assert.equal(configs.categoryStackedBar.options.scales.y.stacked, true);
  assert.equal(configs.categoryStackedArea.options.scales.y.stacked, true);
  assert.equal(configs.categoryStackedArea.data.datasets[0].fill, true);
  assert.equal(configs.categoryStackedArea.data.datasets[0].stack, "categories");
  assert.equal(configs.categoryGroupedBar.options.scales.x.stacked, undefined);
});

test("category charts keep the built-in legend click behavior", () => {
  const configs = buildChartConfigs(makeTrend(), makeMonth(), formatMinorUnits);
  assert.equal(configs.categoryStackedBar.options.plugins.legend.position, "bottom");
  assert.equal(configs.categoryStackedBar.options.plugins.legend.onClick, undefined);
  assert.equal(configs.categoryDoughnut.options.plugins.legend.onClick, undefined);
});

test("tooltips format integer minor units as RMB", () => {
  const configs = buildChartConfigs(makeTrend(), makeMonth(), formatMinorUnits);
  const cartesianLabel = configs.totalLine.options.plugins.tooltip.callbacks.label({
    dataset: { label: "月度净消费" },
    parsed: { y: 123456 },
  });
  const doughnutLabel = configs.categoryDoughnut.options.plugins.tooltip.callbacks.label({
    label: "餐饮美食",
    parsed: 123456,
  });
  assert.equal(cartesianLabel, "月度净消费: ¥1,234.56");
  assert.equal(doughnutLabel, "餐饮美食: ¥1,234.56");
});

test("empty and single-month chart data build without exceptions", () => {
  const empty = buildChartConfigs({ months: [], categories: [] }, null, formatMinorUnits);
  assert.deepEqual(empty.totalLine.data.labels, []);
  assert.deepEqual(empty.categoryDoughnut.data.labels, []);

  const single = buildChartConfigs(
    {
      months: [{ month: "2026-06", monthLabel: "2026 年 6 月", totalSpendingMinor: 100 }],
      categories: [
        { category: "餐饮美食", totalSpendingMinor: 100, spendingByMonthMinor: [100] },
      ],
    },
    { month: "2026-06", monthLabel: "2026 年 6 月", categories: [] },
    formatMinorUnits,
  );
  assert.equal(single.totalBar.data.datasets[0].data.length, 1);
});

test("registry destroys the previous chart before replacing it", () => {
  const destroyed = [];
  class FakeChart {
    constructor(canvas, config) {
      this.canvas = canvas;
      this.config = config;
    }

    destroy() {
      destroyed.push(this.canvas);
    }
  }

  const registry = createChartRegistry(FakeChart);
  registry.render("chart", "first", { type: "bar" });
  registry.render("chart", "second", { type: "line" });
  assert.deepEqual(destroyed, ["first"]);
  registry.destroyAll();
  assert.deepEqual(destroyed, ["first", "second"]);
});
