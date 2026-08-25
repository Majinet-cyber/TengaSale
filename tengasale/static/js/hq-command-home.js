(function () {
  "use strict";
  const canvas = document.getElementById("hqPaymentPerformanceChart");
  const payloadNode = document.getElementById("hqPaymentComparison");
  if (!canvas || !payloadNode) return;
  const data = JSON.parse(payloadNode.textContent);
  const hasData = [...data.current, ...data.previous].some(value => Number(value) > 0);
  if (!hasData) {
    canvas.hidden = true;
    canvas.parentElement.querySelector(".hq-home-chart__empty").hidden = false;
    return;
  }
  if (typeof Chart === "undefined") return;
  const chart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: data.labels,
      datasets: [
        { label: data.previous_label, data: data.previous, backgroundColor: "#dce3ff", borderRadius: { topLeft: 7, topRight: 7 }, maxBarThickness: 22 },
        { label: data.current_label, data: data.current, backgroundColor: "#6373f0", borderRadius: { topLeft: 7, topRight: 7 }, maxBarThickness: 22 },
        { type: "line", label: "Trend", data: data.trend, borderColor: "#ff7114", backgroundColor: "#ff7114", borderWidth: 2.5, pointRadius: 2.5, pointHoverRadius: 5, tension: .35 }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: item => `${item.dataset.label}: MWK ${Number(item.raw).toLocaleString("en-US")}` } } },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: "#74819a", font: { size: 9, weight: 700 } } },
        y: { beginAtZero: true, grid: { color: "rgba(23,33,58,.055)" }, border: { display: false }, ticks: { color: "#98a2b3", font: { size: 9 }, callback: value => value >= 1000000 ? `${(value / 1000000).toFixed(1)}M` : value >= 1000 ? `${Math.round(value / 1000)}K` : value } }
      }
    }
  });
  const period = document.getElementById("hqPaymentPeriod");
  const comparisonLabel = document.getElementById("hqPaymentComparisonLabel");
  const currentKey = document.getElementById("hqCurrentPeriodKey");
  const previousKey = document.getElementById("hqPreviousPeriodKey");
  const applyPeriod = () => {
    const days = Number(period.value);
    chart.data.labels = data.labels.slice(-days);
    chart.data.datasets[0].data = data.previous.slice(-days);
    chart.data.datasets[1].data = data.current.slice(-days);
    chart.data.datasets[2].data = data.trend.slice(-days);
    comparisonLabel.textContent = `${period.options[period.selectedIndex].text} · current vs previous equivalent period`;
    currentKey.textContent = days === 1 ? "Today" : `Current ${days} days`;
    previousKey.textContent = days === 1 ? "Yesterday" : `Previous ${days} days`;
    chart.update();
  };
  period.addEventListener("change", applyPeriod);
  applyPeriod();
})();
