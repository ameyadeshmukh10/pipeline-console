import { api } from "../api.js";
import { fmt, esc, info } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

const VERDICT = { rising: "lifted ↑", falling: "declined ↓", flat: "held roughly flat", insufficient_data: "—" };

const STAGE_COLS = [["s1", "→S1"], ["s2", "→S2"], ["s3", "→S3"], ["s4", "→S4"], ["s5", "→S5"], ["won", "Won"]];

export async function render(el) {
  const d = await api.cohorts();
  const t = d.totals;

  el.innerHTML = `
    <div class="grid kpis">
      ${kpi("Q2 Deals Created", t.n, "all create-week cohorts")}
      ${kpi("Reached S1" + info("Across all Q2 cohorts, % of created deals that ever advanced to Stage 1 (closure-aware)."), fmt.pct(t.conv.s0_s1), `${t.reached.s1} deals`)}
      ${kpi("Closed-Lost" + info("% of created deals that ended Closed-Lost. 'Lost from S0' never reached an AE."), fmt.pct(t.conv.s0_lost), `${fmt.pct(t.lost_from_s0_rate)} lost from S0`)}
      ${kpi("Median Stage Depth" + info("The furthest pipeline stage the typical deal reached."), "S" + (t.depth.median ?? 0), "furthest stage reached")}
    </div>

    ${trendCallout(d.conv_trends)}

    <div class="panel"><h3>Cohort Funnel <span class="panel-sub">% of each create-week cohort that ever reached each stage · shaded weeks still maturing</span></h3>
      <div class="table-scroll"><table class="heat"><thead><tr><th class="week">Create week</th><th class="num">n</th>
        ${STAGE_COLS.map(([, l]) => `<th class="num">${l}</th>`).join("")}<th class="num">Lost</th></tr></thead>
        <tbody>${d.cohorts.map(funnelRow).join("")}
          <tr style="border-top:2px solid var(--line-strong)"><td class="week"><strong>All Q2</strong></td><td class="num"><strong>${t.n}</strong></td>
            ${STAGE_COLS.map(([k]) => `<td class="num"><strong>${fmt.pct(t.reached_pct[k])}</strong></td>`).join("")}
            <td class="num"><strong>${fmt.pct(t.reached_pct.lost)}</strong></td></tr></tbody></table></div></div>

    <div class="cols-2">
      <div class="panel"><h3>Stage Depth by Cohort <span class="panel-sub">furthest stage reached</span></h3>
        <div class="chart-wrap"><canvas id="co-depth"></canvas></div></div>
      <div class="panel"><h3>Conversion Across Cohorts <span class="panel-sub">0→1 and 0→lost</span></h3>
        <div class="chart-wrap"><canvas id="co-conv"></canvas></div></div>
    </div>

    <div class="panel"><h3>Time Between Stages <span class="panel-sub">median days per leg, by create-week cohort</span></h3>
      <div class="table-scroll"><table><thead><tr><th>Create week</th><th class="num">n</th>
        ${d.legs.map((l) => `<th class="num">${l.replace("_", "→").toUpperCase()}</th>`).join("")}</tr></thead>
        <tbody>${d.cohorts.map((c) => timingRow(c, d.legs)).join("")}</tbody></table></div></div>`;

  const orders = [0, 1, 2, 3, 4, 5, 6];
  const oColors = ["#c7d0e4", "#9fb2ee", "#6f93f0", "#4f6ef7", "#e08a1e", "#15a05f", "#1faa6b"];
  chart("co-depth", {
    type: "bar",
    data: { labels: d.cohorts.map((c) => c.week),
      datasets: orders.map((o, i) => ({ label: o === 6 ? "Won" : "S" + o, backgroundColor: oColors[i],
        data: d.cohorts.map((c) => c.depth.dist[String(o)] || 0) })) },
    options: { ...gridOpts, scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, beginAtZero: true, grid: { color: "#eef0f4" } } } },
  });

  chart("co-conv", {
    type: "line",
    data: { labels: d.cohorts.map((c) => c.week),
      datasets: [
        { label: "0→1 %", borderColor: PALETTE[0], tension: .3, data: d.cohorts.map((c) => pctV(c.conv.s0_s1)) },
        { label: "0→Lost %", borderColor: PALETTE[3], tension: .3, data: d.cohorts.map((c) => pctV(c.conv.s0_lost)) },
      ] },
    options: { ...gridOpts, scales: { ...gridOpts.scales, y: { ...gridOpts.scales.y, ticks: { callback: (v) => v + "%" } } } },
  });
}

function funnelRow(c) {
  const op = c.mature ? "" : ' style="opacity:.5"';
  const cells = STAGE_COLS.map(([k]) => heat(c.reached_pct[k], false)).join("");
  return `<tr${op}><td class="week">${c.week}${c.mature ? "" : " ·"}</td><td class="num">${c.n}</td>${cells}${heat(c.reached_pct.lost, true)}</tr>`;
}
function heat(p, lost) {
  if (p == null) return `<td class="num">—</td>`;
  const a = Math.min(0.85, p);
  const bg = lost ? `rgba(224,69,91,${a})` : `rgba(79,110,247,${a})`;
  const col = a > 0.5 ? "#fff" : "var(--ink)";
  return `<td class="num" style="background:${bg};color:${col}">${Math.round(p * 100)}%</td>`;
}
function timingRow(c, legs) {
  return `<tr${c.mature ? "" : ' style="opacity:.5"'}><td>${c.week}</td><td class="num">${c.n}</td>
    ${legs.map((l) => `<td class="num">${fmt.days(c.timing[l].median)}<span class="muted small"> (${c.timing[l].n})</span></td>`).join("")}</tr>`;
}
function trendCallout(ct) {
  if (!ct) return "";
  const s1 = ct.s0_s1;
  const span = (t) => t.first != null ? `from ${fmt.pct(t.first)} (earliest cohort) to ${fmt.pct(t.last)} (latest)` : "";
  const sig = (t) => t.p != null && t.direction !== "insufficient_data"
    ? ` · Mann-Kendall p=${t.p.toFixed(2)}${t.p < 0.1 ? ", significant" : ", not significant"}` : "";
  return `<div class="callout"><strong>Did S0→S1 conversion lift as the quarter progressed?</strong> ${info("Trend of each cohort's Stage 0→Stage 1 conversion across create weeks, mature cohorts only (right-censored recent weeks excluded). Distribution-free Mann-Kendall + Theil-Sen.")}<br>
    Stage 0 → Stage 1 conversion <strong>${VERDICT[s1.direction]}</strong> across mature create-week cohorts${s1.first != null ? " — " + span(s1) : ""}${sig(s1)}.
    ${s1.direction === "insufficient_data" ? '<span class="muted small"> (need ≥4 mature cohorts with ≥3 deals.)</span>' : ""}</div>`;
}
function pctV(x) { return x == null ? null : +(x * 100).toFixed(1); }
function kpi(label, value, sub) {
  return `<div class="card kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-sub">${sub}</div></div>`;
}
