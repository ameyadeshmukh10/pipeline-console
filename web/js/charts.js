// Chart.js helpers. Chart is a global from the CDN script.
export const PALETTE = ["#4f6ef7", "#1faa6b", "#e08a1e", "#e0455b", "#8b5cf6", "#06b6d4", "#64748b", "#ec4899"];
const registry = {};

if (window.Chart) {
  Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = "#5b6577";
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.maintainAspectRatio = false;
}

export function chart(id, config) {
  const el = document.getElementById(id);
  if (!el || !window.Chart) return null;
  if (registry[id]) registry[id].destroy();
  registry[id] = new Chart(el.getContext("2d"), config);
  return registry[id];
}

export function destroyAll() {
  for (const k of Object.keys(registry)) {
    registry[k].destroy();
    delete registry[k];
  }
}

export const gridOpts = {
  scales: {
    x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 } },
    y: { beginAtZero: true, grid: { color: "#eef0f4" }, border: { display: false } },
  },
  plugins: { legend: { position: "bottom" } },
};
