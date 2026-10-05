(function () {
  "use strict";
  const canvas = document.getElementById("hqPaymentPerformanceChart");
  const payloadNode = document.getElementById("hqPaymentComparison");
  if (!canvas || !payloadNode) return;
  const data = JSON.parse(payloadNode.textContent);
  // The server provides consecutive 30-day windows. Shorter comparisons use
  // the immediately preceding days, which can be within the current window.
  const history = [...data.previous, ...data.current];
  const emptyState = canvas.parentElement.querySelector(".hq-home-chart__empty");
  const summary = canvas.closest(".hq-home-performance");
  const totalNode = summary.querySelector(".hq-performance-summary strong");
  const changeNode = summary.querySelector(".hq-performance-summary em");
  const tokens = getComputedStyle(document.documentElement);
  const color = name => tokens.getPropertyValue(name).trim();
  const chartContext = canvas.getContext("2d");
  const currentGradient = chartContext.createLinearGradient(0, 0, 0, 235);
  currentGradient.addColorStop(0, color("--tenga-pulse-cyan"));
  currentGradient.addColorStop(1, color("--tenga-pulse-blue"));
  const chart = typeof Chart === "undefined" ? null : new Chart(canvas, {
    type: "bar",
    data: {
      labels: data.labels,
      datasets: [
        { label: data.previous_label, data: data.previous, backgroundColor: "#dce3ff", borderRadius: { topLeft: 8, topRight: 8 }, barPercentage: .55, categoryPercentage: .72, maxBarThickness: 30 },
        { label: data.current_label, data: data.current, backgroundColor: currentGradient, borderRadius: { topLeft: 8, topRight: 8 }, barPercentage: .55, categoryPercentage: .72, maxBarThickness: 30 },
        { type: "line", label: "Trend", data: data.trend, borderColor: color("--tenga-pulse-cyan"), backgroundColor: color("--tenga-pulse-cyan"), borderWidth: 2.5, pointRadius: 2.5, pointHoverRadius: 5, tension: .35 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 350 }, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: item => `${item.dataset.label}: MWK ${Number(item.raw).toLocaleString("en-US")}` } } },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: "#74819a", maxRotation: 0, autoSkip: true, font: { size: 11, weight: 700 } } },
        y: { beginAtZero: true, grid: { color: "rgba(116,129,154,.12)" }, border: { display: false }, ticks: { color: "#74819a", font: { size: 11 }, callback: value => value >= 1000000 ? `${(value / 1000000).toFixed(1)}M` : value >= 1000 ? `${Math.round(value / 1000)}K` : value } }
      }
    }
  });
  const period = document.getElementById("hqPaymentPeriod");
  const comparisonLabel = document.getElementById("hqPaymentComparisonLabel");
  const currentKey = document.getElementById("hqCurrentPeriodKey");
  const previousKey = document.getElementById("hqPreviousPeriodKey");
  const applyPeriod = () => {
    const days = Number(period.value);
    const current = history.slice(-days);
    const previous = history.slice(-2 * days, -days);
    const currentLabel = days === 1 ? "Today" : `Current ${days} days`;
    const previousLabel = days === 1 ? "Yesterday" : `Previous ${days} days`;
    const total = current.reduce((sum, value) => sum + Number(value), 0);
    const previousTotal = previous.reduce((sum, value) => sum + Number(value), 0);
    const hasData = [...current, ...previous].some(value => Number(value) > 0);
    canvas.hidden = !chart || !hasData;
    emptyState.hidden = Boolean(chart && hasData);
    emptyState.textContent = hasData ? "Chart unavailable. Total collections are shown above." : "No payment activity for this period";
    totalNode.textContent = `MWK ${total.toLocaleString("en-US")}`;
    changeNode.textContent = previousTotal > 0
      ? `${((total - previousTotal) / previousTotal * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}% vs ${previousLabel.toLowerCase()}`
      : `No ${previousLabel.toLowerCase()} comparison`;
    comparisonLabel.textContent = `${period.options[period.selectedIndex].text} · current vs previous equivalent period`;
    currentKey.textContent = currentLabel;
    previousKey.textContent = previousLabel;
    if (chart) {
      chart.data.labels = data.labels.slice(-days);
      chart.data.datasets[0].data = previous;
      chart.data.datasets[0].label = previousLabel;
      chart.data.datasets[1].data = current;
      chart.data.datasets[1].label = currentLabel;
      chart.data.datasets[2].data = data.trend.slice(-days);
      chart.update();
    }
  };
  period.addEventListener("change", applyPeriod);
  applyPeriod();
})();
