import { api } from "../api.js";
import { fmt, esc, trendTag } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

export async function render(el, ctx) {
  const d = await api.prepipeline();
  const mature = d.conversion.filter((c) => c.mature && c.cohort_n > 0);
  const lastMature = mature[mature.length - 1];
  const weeksN = d.weekly.filter((w) => !w.partial).length || 1;

  el.innerHTML = `
    <div class="grid kpis">
      ${kpi("Stage-0 Created (Q2)", d.totals.stage0_created, `${(d.totals.stage0_created / weeksN).toFixed(1)} / wk avg`)}
      ${kpi("Creation Trend", trendTag(d.net_trend), "week-over-week", true)}
      ${kpi("S0→S1 Conversion", lastMature ? fmt.pct(lastMature.s0_s1_ever_rate) : "—", lastMature ? `mature wk ${lastMature.week}` : "maturing", true)}
      ${kpi("S0→Closed-Lost", lastMature ? fmt.pct(lastMature.s0_lost_rate) : "—", "within target horizon", true)}
    </div>

    <div class="cols-2">
      <div class="panel"><h3>Weekly Stage-0 Created <span class="panel-sub">by SDR (event-dated)</span></h3>
        <div class="chart-wrap"><canvas id="pp-bar"></canvas></div></div>
      <div class="panel"><h3>Weekly Conversion <span class="panel-sub">cohort by S0-entry week · immature weeks dashed</span></h3>
        <div class="chart-wrap"><canvas id="pp-conv"></canvas></div></div>
    </div>

    <div class="panel"><h3>SDR Quality Comparison <span class="panel-sub">Q2 aggregate</span></h3>
      <table>
        <thead><tr><th>SDR</th><th class="num">Stage-0 Created</th><th class="num">Reached S1</th>
          <th class="num">Lost from S0</th><th class="num">Median S0→S1</th><th class="num">Open S0 aging &gt;14d</th></tr></thead>
        <tbody>${d.per_sdr.map(sdrRow).join("")}</tbody>
      </table>
    </div>`;

  const labels = d.weekly.map((w) => w.week);
  chart("pp-bar", {
    type: "bar",
    data: {
      labels,
      datasets: d.sdr_keys.map((k, i) => ({
        label: d.sdr_labels[k], backgroundColor: PALETTE[i % PALETTE.length],
        data: d.weekly.map((w) => w.by_sdr[k] || 0),
      })),
    },
    options: { ...gridOpts, scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, beginAtZero: true, grid: { color: "#eef0f4" } } } },
  });

  chart("pp-conv", {
    type: "line",
    data: {
      labels: d.conversion.map((c) => c.week),
      datasets: [
        { label: "S0→S1 %", borderColor: PALETTE[0], backgroundColor: PALETTE[0], tension: .3,
          segment: { borderDash: (c) => (d.conversion[c.p1DataIndex] && !d.conversion[c.p1DataIndex].mature ? [5, 4] : undefined) },
          data: d.conversion.map((c) => c.s0_s1_ever_rate == null ? null : +(c.s0_s1_ever_rate * 100).toFixed(1)) },
        { label: "S0→Lost %", borderColor: PALETTE[3], backgroundColor: PALETTE[3], tension: .3,
          data: d.conversion.map((c) => c.s0_lost_rate == null ? null : +(c.s0_lost_rate * 100).toFixed(1)) },
      ],
    },
    options: { ...gridOpts, scales: { ...gridOpts.scales, y: { ...gridOpts.scales.y, ticks: { callback: (v) => v + "%" } } } },
  });
}

function kpi(label, value, sub, raw = false) {
  return `<div class="card kpi"><div class="kpi-label">${label}</div>
    <div class="kpi-value">${raw ? value : esc(value)}</div><div class="kpi-sub">${sub}</div></div>`;
}
function sdrRow(r) {
  const aging = r.open_s0 ? `${r.open_s0_aging}/${r.open_s0}` : "—";
  return `<tr><td>${esc(r.name)}</td><td class="num">${r.total_s0}</td>
    <td class="num">${fmt.pct(r.s0_s1_ever_rate)}</td><td class="num">${fmt.pct(r.lost_from_s0_rate)}</td>
    <td class="num">${fmt.days(r.median_days_s0_s1)}</td><td class="num">${aging}</td></tr>`;
}
