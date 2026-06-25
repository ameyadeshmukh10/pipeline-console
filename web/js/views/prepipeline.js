import { api } from "../api.js";
import { esc, info, trendTag } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

const AGG = "#11203a";
const r1 = (v) => (v == null ? null : Math.round(v * 10) / 10);
const ck = (c) => c.id || "unknown";
const color = (i) => PALETTE[i % PALETTE.length];
const wlabel = (w) => w.replace(/^\d{4}-/, "");

const TIP = {
  weekly:
    "Isolated weekly: the window is one week, so you need ~80–100 resolved deals that week to trust any single point at ±10. Most B2B funnels never hit that weekly at Stage 0→1. Below ~30/week the points are basically decorative — directional at best, and you should not react to a single week's movement. This is exactly why isolated weekly is the noisy option.",
  rolling4:
    "4-week rolling: the window pools 4 weeks, so the threshold is ~100 resolved deals across the trailing 4 weeks → roughly 25/week. This is the realistic sweet spot for most B2B volumes and the main reason rolling wins: it manufactures sample size you don't have weekly. At 25/week each point sits near ±10; at 40–50/week it tightens to ±7 or better.",
  cumulative:
    "Cumulative QTD: self-solving over time. Early weeks are garbage (week 1 = one week of data), but n grows every week, so by mid-quarter you're pooling 100+ almost regardless of weekly rate, and by quarter-end the interval is tight. The tradeoff isn't sample size, it's staleness — it's statistically accurate but increasingly insensitive to recent change.",
};
const SPEED_TIP =
  "For each week, the average days from Stage 0 to Stage 1 across deals that ENTERED Stage 1 that week, attributed to the deal's creator. Weekly = that week alone; 4-wk rolling and Cumulative pool the underlying deals (not averages of averages) to manufacture sample size.";

export async function render(el) {
  const d = await api.prepipeline();
  const weeks = d.weeks;

  el.innerHTML = `
    <div class="grid kpis">
      ${kpi("Stage-0 Created (Q2)", d.totals.stage0_created, "by createdate")}
      ${kpi("Creators", d.totals.creators_count, "incl. off-roster & archived")}
      ${kpi("Creation Trend", trendTag(d.created.trend), "over the quarter", true)}
    </div>

    <div class="panel">
      <h3>Weekly Stage-0 Created <span class="panel-sub">by creator · scoped to createdate in quarter</span></h3>
      <div id="pp-created-chips" class="chip-row"></div>
      <div class="chart-wrap"><canvas id="pp-created"></canvas></div>
      <details class="more"><summary>Per-creator weekly counts</summary>
        <div class="table-scroll" id="pp-created-table"></div></details>
    </div>

    <div class="panel">
      <h3>Weekly Speed to Stage 1 <span class="panel-sub">avg days S0→S1, by S1-transition week</span> ${info(SPEED_TIP)}</h3>
      <div id="pp-speed-modes" class="mode-row"></div>
      <div class="chart-wrap"><canvas id="pp-speed"></canvas></div>
      <details class="more" open><summary>Per-creator weekly average (n)</summary>
        <div class="table-scroll" id="pp-speed-table"></div></details>
    </div>

    <div class="panel">
      <h3>Weekly S0→S1 Conversion <span class="panel-sub">advanced ÷ (advanced + closed-lost-at-S0) · open S0 excluded</span></h3>
      <div class="mode-row">
        <label>View
          <select id="pp-conv-mode">
            <option value="weekly">Isolated weekly</option>
            <option value="rolling4" selected>4-week rolling</option>
            <option value="cumulative">Cumulative QTD</option>
          </select>
        </label>
        <span id="pp-conv-info"></span>
      </div>
      <div class="chart-wrap"><canvas id="pp-conv"></canvas></div>
      <p id="pp-conv-caption" class="muted small caption"></p>
      <details class="more"><summary>Per-creator conversion (denominator)</summary>
        <div class="table-scroll" id="pp-conv-table"></div></details>
    </div>`;

  buildCreated(d, weeks);
  buildSpeed(d, weeks);
  buildConversion(d, weeks);
}

// --------------------------------------------------------------------------- //
// Section 1 — Weekly Stage-0 Created (per-creator lines + toggle chips)
// --------------------------------------------------------------------------- //
function buildCreated(d, weeks) {
  const zeros = weeks.map(() => 0);
  const datasets = [
    { label: "Aggregate", data: d.created.aggregate, borderColor: AGG, backgroundColor: AGG,
      borderWidth: 2.5, tension: 0.3, pointRadius: 2 },
    ...d.creators.map((c, i) => ({
      label: c.first_name, data: d.created.by_creator[ck(c)] || zeros,
      borderColor: color(i), backgroundColor: color(i), borderWidth: 1.5,
      tension: 0.3, pointRadius: 0, hidden: true,
    })),
  ];
  const ch = chart("pp-created", {
    type: "line", data: { labels: weeks, datasets },
    options: { ...gridOpts, plugins: { legend: { display: false } } },
  });

  const chips = [{ label: "Aggregate", idx: 0, active: true, dot: AGG, title: "All creators combined" }]
    .concat(d.creators.map((c, i) => ({
      label: c.first_name, idx: i + 1, active: false, dot: color(i),
      title: c.name + (c.role ? ` · ${c.role}` : "") + (c.archived ? " · archived" : "") + ` · ${c.total_created} created`,
    })));
  document.getElementById("pp-created-chips").innerHTML = chips.map((c) =>
    `<button class="toggle-chip${c.active ? " active" : ""}" data-idx="${c.idx}" title="${esc(c.title)}">
       <span class="dot" style="background:${c.dot}"></span>${esc(c.label)}</button>`).join("");
  document.querySelectorAll("#pp-created-chips .toggle-chip").forEach((btn) => {
    btn.onclick = () => {
      const ds = ch.data.datasets[+btn.dataset.idx];
      ds.hidden = !ds.hidden;
      btn.classList.toggle("active", !ds.hidden);
      ch.update();
    };
  });

  document.getElementById("pp-created-table").innerHTML = countTable(d, weeks);
}

// --------------------------------------------------------------------------- //
// Section 2 — Weekly Speed to Stage 1 (3 overlay toggles + per-creator table)
// --------------------------------------------------------------------------- //
function buildSpeed(d, weeks) {
  const sp = d.speed.aggregate;
  const modes = [
    ["weekly", "Weekly Average", PALETTE[0], true],
    ["rolling4", "4-wk rolling", PALETTE[1], true],
    ["cumulative", "Cumulative", PALETTE[2], false],
  ];
  const datasets = modes.map(([k, lbl, col, vis]) => ({
    label: lbl, data: sp[k].map((c) => r1(c.avg)), borderColor: col, backgroundColor: col,
    tension: 0.3, spanGaps: true, pointRadius: 2, hidden: !vis,
  }));
  const ch = chart("pp-speed", {
    type: "line", data: { labels: weeks, datasets },
    options: { ...gridOpts, scales: { ...gridOpts.scales,
      y: { ...gridOpts.scales.y, ticks: { callback: (v) => v + "d" } } } },
  });
  document.getElementById("pp-speed-modes").innerHTML = modes.map(([k, lbl, col, vis], i) =>
    `<button class="toggle-chip${vis ? " active" : ""}" data-i="${i}">
       <span class="dot" style="background:${col}"></span>${lbl}</button>`).join("");
  document.querySelectorAll("#pp-speed-modes .toggle-chip").forEach((btn) => {
    btn.onclick = () => {
      const ds = ch.data.datasets[+btn.dataset.i];
      ds.hidden = !ds.hidden;
      btn.classList.toggle("active", !ds.hidden);
      ch.update();
    };
  });
  document.getElementById("pp-speed-table").innerHTML = meanTable(d, weeks);
}

// --------------------------------------------------------------------------- //
// Section 3 — Weekly S0→S1 Conversion (dropdown + tooltips + per-creator table)
// --------------------------------------------------------------------------- //
function buildConversion(d, weeks) {
  const sel = document.getElementById("pp-conv-mode");
  document.getElementById("pp-conv-info").innerHTML =
    info(TIP.weekly + "\n\n" + TIP.rolling4 + "\n\n" + TIP.cumulative);

  const draw = () => {
    const mode = sel.value;
    const series = d.conversion.aggregate[mode];
    chart("pp-conv", {
      type: "line",
      data: { labels: weeks, datasets: [{
        label: "S0→S1 %", data: series.map((c) => (c.rate == null ? null : r1(c.rate * 100))),
        borderColor: PALETTE[0], backgroundColor: PALETTE[0], tension: 0.3, spanGaps: true, pointRadius: 2,
      }] },
      options: { ...gridOpts, scales: { ...gridOpts.scales,
        y: { ...gridOpts.scales.y, ticks: { callback: (v) => v + "%" } } } },
    });
    document.getElementById("pp-conv-caption").textContent = TIP[mode];
    document.getElementById("pp-conv-table").innerHTML = rateTable(d, weeks, mode);
  };
  sel.onchange = draw;
  draw();
}

// --------------------------------------------------------------------------- //
// Tables (granular, per-creator)
// --------------------------------------------------------------------------- //
function header(weeks, extra = "") {
  return `<tr><th class="sticky">Creator</th>${weeks.map((w) => `<th class="num">${wlabel(w)}</th>`).join("")}${extra}</tr>`;
}
function nameCell(c) {
  return `<td class="sticky" title="${esc(c.name)}">${esc(c.first_name)}${c.archived ? ' <span class="muted">·arch</span>' : ""}</td>`;
}

function countTable(d, weeks) {
  const rows = [`<tr class="agg-row"><td class="sticky">Aggregate</td>${d.created.aggregate.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${d.totals.stage0_created}</td></tr>`];
  for (const c of d.creators) {
    const arr = d.created.by_creator[ck(c)] || [];
    rows.push(`<tr>${nameCell(c)}${weeks.map((_, i) => `<td class="num">${arr[i] || "—"}</td>`).join("")}<td class="num">${c.total_created}</td></tr>`);
  }
  return `<table class="grid-table">${header(weeks, '<th class="num">Total</th>')}${rows.join("")}</table>`;
}

function meanCell(c) {
  return c && c.n ? `${Math.round(c.avg * 10) / 10}<span class="muted"> (${c.n})</span>` : "—";
}
function meanTable(d, weeks) {
  const rows = [`<tr class="agg-row"><td class="sticky">Aggregate</td>${d.speed.aggregate.weekly.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>`];
  for (const c of d.creators) {
    const s = d.speed.by_creator[ck(c)];
    if (!s) continue;
    rows.push(`<tr>${nameCell(c)}${s.weekly.map((x) => `<td class="num">${meanCell(x)}</td>`).join("")}</tr>`);
  }
  return `<table class="grid-table">${header(weeks)}${rows.join("")}</table>`;
}

function rateCell(c) {
  return c && c.denom ? `${Math.round(c.rate * 100)}%<span class="muted"> (${c.denom})</span>` : "—";
}
function rateTable(d, weeks, mode) {
  const rows = [`<tr class="agg-row"><td class="sticky">Aggregate</td>${d.conversion.aggregate[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>`];
  for (const c of d.creators) {
    const s = d.conversion.by_creator[ck(c)];
    if (!s) continue;
    rows.push(`<tr>${nameCell(c)}${s[mode].map((x) => `<td class="num">${rateCell(x)}</td>`).join("")}</tr>`);
  }
  return `<table class="grid-table">${header(weeks)}${rows.join("")}</table>`;
}

function kpi(label, value, sub, raw = false) {
  return `<div class="card kpi"><div class="kpi-label">${label}</div>
    <div class="kpi-value">${raw ? value : esc(value)}</div><div class="kpi-sub">${sub}</div></div>`;
}
