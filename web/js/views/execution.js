import { api } from "../api.js";
import { fmt, esc, trendTag } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

export async function render(el, ctx) {
  const d = await api.execution();
  const v = d.velocity;
  const fc = d.funnel_cumulative;

  el.innerHTML = `
    <div class="grid kpis">
      ${kpi("AE Assignments (Q2)", d.totals.assignments, "deals entered Stage 1")}
      ${kpi("Median S0→S1", fmt.days(v.s0_s1.median), `target ${v.s0_s1_target}d · n=${v.s0_s1.n}`, healthClass(v.s0_s1.median, v.s0_s1_target))}
      ${kpi("Median S1→S3", fmt.days(v.s1_s3.median), `target ${v.s1_s3_target}d · n=${v.s1_s3.n}`, healthClass(v.s1_s3.median, v.s1_s3_target))}
      ${kpi("Pipeline Velocity", trendTag(d.net_trend), "S1→S3, Mann-Kendall", true)}
    </div>

    <div class="cols-2">
      <div class="panel"><h3>Weekly AE Assignments <span class="panel-sub">entered Stage 1, by AE</span></h3>
        <div class="chart-wrap"><canvas id="ex-assign"></canvas></div></div>
      <div class="panel"><h3>Q2 Funnel <span class="panel-sub">S0 cohort · S0→S1 ${fmt.pct(fc.conv_s0_s1)} · S1→S2 ${fmt.pct(fc.conv_s1_s2)} · S2→S3 ${fmt.pct(fc.conv_s2_s3)}</span></h3>
        <div class="chart-wrap"><canvas id="ex-funnel"></canvas></div></div>
    </div>

    <div class="cols-2">
      <div class="panel"><h3>Median Time in Stage <span class="panel-sub">completed, days</span></h3>
        <div class="chart-wrap"><canvas id="ex-dwell"></canvas></div></div>
      <div class="panel"><h3>Velocity by AE <span class="panel-sub">S1→S3 trend (gated n≥8)</span></h3>
        <table><thead><tr><th>AE</th><th>Direction</th></tr></thead>
          <tbody>${Object.entries(d.per_ae_trend).map(([n, t]) => `<tr><td>${esc(n)}</td><td>${trendTag(t)}</td></tr>`).join("") || `<tr><td colspan=2 class=muted>No completed S1→S3 legs.</td></tr>`}</tbody></table>
      </div>
    </div>

    <div class="panel"><h3>Open Pipeline Aging <span class="panel-sub">deals over target with no resolution</span></h3>
      <div class="flag-list" style="margin-bottom:12px">${Object.entries(d.aging_summary).map(([s, o]) => `<span class="pill">${s}: ${o.aging}/${o.open} aging · med ${fmt.days(o.median_age)}</span>`).join("")}</div>
      <div class="table-scroll"><table><thead><tr><th>Deal</th><th>Stage</th><th>Owner</th><th class="num">Age in stage</th></tr></thead>
        <tbody>${d.aging_deals.slice(0, 40).map((r) => `<tr class="row-click" data-deal="${r.deal_id}"><td>${esc(r.dealname)}</td><td>${r.stage}</td><td>${esc(r.owner)}</td><td class="num">${fmt.days(r.age_days)}</td></tr>`).join("")}</tbody></table></div>
    </div>`;

  el.querySelectorAll("[data-deal]").forEach((tr) => tr.onclick = () => ctx.openInspector(tr.dataset.deal));

  chart("ex-assign", {
    type: "bar",
    data: { labels: d.weekly_assign.map((w) => w.week),
      datasets: d.ae_keys.map((k, i) => ({ label: d.ae_labels[k], backgroundColor: PALETTE[i % PALETTE.length],
        data: d.weekly_assign.map((w) => w.by_ae[k] || 0) })) },
    options: { ...gridOpts, scales: { x: { stacked: true, grid: { display: false } }, y: { stacked: true, beginAtZero: true, grid: { color: "#eef0f4" } } } },
  });

  const c = fc.counts;
  chart("ex-funnel", {
    type: "bar",
    data: { labels: ["S0", "S1", "S2", "S3", "Won", "Lost"],
      datasets: [{ label: "Deals", data: [c.s0, c.s1, c.s2, c.s3, c.won, c.lost],
        backgroundColor: ["#4f6ef7", "#5b7bf8", "#6f93f0", "#86a8ea", "#1faa6b", "#e0455b"] }] },
    options: { ...gridOpts, indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, grid: { color: "#eef0f4" } }, y: { grid: { display: false } } } },
  });

  const stages = ["S0", "S1", "S2", "S3", "S4", "S5"];
  chart("ex-dwell", {
    type: "bar",
    data: { labels: stages, datasets: [{ label: "Median days", backgroundColor: PALETTE[0],
      data: stages.map((s) => d.stage_dwell[s] ? d.stage_dwell[s].median : null) }] },
    options: { ...gridOpts, plugins: { legend: { display: false } } },
  });
}

function kpi(label, value, sub, cls) {
  const c = cls === true ? "" : (cls ? " " + cls : "");
  return `<div class="card kpi"><div class="kpi-label">${label}</div>
    <div class="kpi-value${c}">${cls === true ? value : esc(value)}</div><div class="kpi-sub">${sub}</div></div>`;
}
function healthClass(v, target) {
  if (v == null) return "";
  return v <= target ? "good" : "warn";
}
