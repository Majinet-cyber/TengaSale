// Run with: node --test tengasale/dashboard/test_hq_command_chart.cjs
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(path.join(__dirname, "../static/js/hq-command-home.js"), "utf8");

function mountChart(payments = {}, { libraryAvailable = true } = {}) {
  const current = Array(30).fill(0);
  const previous = Array(30).fill(0);
  for (const [age, amount] of Object.entries(payments)) {
    const daysAgo = Number(age);
    if (daysAgo < 30) current[29 - daysAgo] = amount;
    else previous[59 - daysAgo] = amount;
  }
  const payload = {
    labels: Array.from({ length: 30 }, (_, i) => `Day ${i + 1}`),
    current, previous, trend: current,
    current_label: "Current 30-day dates", previous_label: "Previous 30-day dates",
  };
  const emptyState = { hidden: true, textContent: "" };
  const total = { textContent: "Server-rendered today's total" };
  const change = { textContent: "Server-rendered daily change" };
  const canvas = {
    hidden: false,
    parentElement: { querySelector: () => emptyState },
    closest: () => ({ querySelector: selector => selector.endsWith("strong") ? total : change }),
    getContext: () => ({ createLinearGradient: () => ({ addColorStop() {} }) }),
  };
  const listeners = {};
  const period = {
    value: "7", selectedIndex: 1,
    options: [{ text: "Today" }, { text: "Last 7 days" }, { text: "Last 30 days" }],
    addEventListener: (name, handler) => { listeners[name] = handler; },
  };
  const nodes = {
    hqPaymentPerformanceChart: canvas,
    hqPaymentComparison: { textContent: JSON.stringify(payload) },
    hqPaymentPeriod: period,
    hqPaymentComparisonLabel: { textContent: "" },
    hqCurrentPeriodKey: { textContent: "" },
    hqPreviousPeriodKey: { textContent: "" },
  };
  let chart;
  const context = {
    document: { getElementById: id => nodes[id], documentElement: {} },
    getComputedStyle: () => ({ getPropertyValue: () => "#3563ff" }),
    window: { matchMedia: () => ({ matches: true }) },
  };
  if (libraryAvailable) {
    context.Chart = class {
      constructor(_canvas, config) {
        this.data = config.data;
        this.options = config.options;
        chart = this;
      }
      update() {}
    };
  }
  vm.runInNewContext(source, context);
  return {
    chart, canvas, emptyState, total, change, nodes,
    select(days) {
      period.value = String(days);
      period.selectedIndex = [1, 7, 30].indexOf(days);
      listeners.change();
    },
  };
}

const payments = { 0: 100, 1: 25, 7: 50, 13: 10, 30: 1000, 36: 2000, 59: 4000 };
const values = dataset => Array.from(dataset.data);

test("default seven days compares against the immediately preceding seven days", () => {
  const page = mountChart(payments);
  assert.deepEqual(values(page.chart.data.datasets[0]), [10, 0, 0, 0, 0, 0, 50]);
  assert.deepEqual(values(page.chart.data.datasets[1]), [0, 0, 0, 0, 0, 25, 100]);
  assert.equal(page.total.textContent, "MWK 125");
  assert.equal(page.change.textContent, "108.3% vs previous 7 days");
  assert.equal(page.chart.data.datasets[0].label, "Previous 7 days");
});

test("Today compares with yesterday and updates tooltips, key and total", () => {
  const page = mountChart(payments);
  page.select(1);
  assert.deepEqual(values(page.chart.data.datasets[0]), [25]);
  assert.deepEqual(values(page.chart.data.datasets[1]), [100]);
  assert.equal(page.total.textContent, "MWK 100");
  assert.equal(page.change.textContent, "300% vs yesterday");
  assert.equal(page.nodes.hqPreviousPeriodKey.textContent, "Yesterday");
  assert.equal(page.chart.options.plugins.tooltip.callbacks.label({
    dataset: page.chart.data.datasets[0], raw: 25,
  }), "Yesterday: MWK 25");
});

test("30 days retains the full preceding month and returning to seven days is stable", () => {
  const page = mountChart(payments);
  page.select(30);
  assert.equal(page.chart.data.datasets[0].data.length, 30);
  assert.equal(page.chart.data.datasets[0].data[0], 4000);
  assert.equal(page.chart.data.datasets[0].data[23], 2000);
  assert.equal(page.chart.data.datasets[0].data[29], 1000);
  assert.equal(page.total.textContent, "MWK 185");
  assert.equal(page.change.textContent, "-97.4% vs previous 30 days");
  page.select(7);
  assert.equal(page.total.textContent, "MWK 125");
  assert.deepEqual(values(page.chart.data.datasets[0]), [10, 0, 0, 0, 0, 0, 50]);
});

test("empty state follows selected period rather than all 60 days", () => {
  const page = mountChart({ 20: 900 });
  assert.equal(page.canvas.hidden, true);
  assert.equal(page.emptyState.hidden, false);
  assert.equal(page.emptyState.textContent, "No payment activity for this period");
  assert.equal(page.total.textContent, "MWK 0");
  page.select(30);
  assert.equal(page.canvas.hidden, false);
  assert.equal(page.emptyState.hidden, true);
  assert.equal(page.total.textContent, "MWK 900");
  page.select(1);
  assert.equal(page.canvas.hidden, true);
  assert.equal(page.total.textContent, "MWK 0");
});

test("zero history keeps period controls functional without a fabricated comparison", () => {
  const page = mountChart();
  page.select(1);
  assert.equal(page.change.textContent, "No yesterday comparison");
  assert.equal(page.nodes.hqCurrentPeriodKey.textContent, "Today");
  assert.equal(page.emptyState.hidden, false);
});

test("missing chart library still provides accurate selected-period financial summaries", () => {
  const page = mountChart(payments, { libraryAvailable: false });
  assert.equal(page.total.textContent, "MWK 125");
  assert.equal(page.canvas.hidden, true);
  assert.match(page.emptyState.textContent, /Chart unavailable/);
  page.select(1);
  assert.equal(page.total.textContent, "MWK 100");
  assert.equal(page.change.textContent, "300% vs yesterday");
});
