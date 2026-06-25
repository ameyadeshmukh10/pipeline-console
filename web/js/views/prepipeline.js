import { api } from "../api.js";
import { esc, info, trendTag } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

const AGG = "#11203a";
const GPAL = ["#7c3aed", "#0e7490", "#b91c1c", "#15803d", "#a16207", "#9d174d", "#1e3a8a", "#9333ea"];
const r1 = (v) => (v == null ? null : Math.round(v * 10) / 10);
const ck = (c) => c.id || "unknown";
const gk = (g) => "g:" + g.id;
const color = (i) => PALETTE[i % PALETTE.length];
const gcolor = (i) => GPAL[i % GPAL.length];
const wlabel = (w) => w.replace(/^\d{4}-/, "");

const MODE_LABEL = { weekly: "Weekly", rolling4: "4-wk rolling", cumulative: "Cumulative" };
const TREND_TIP =
  "Weekly / 4-wk rolling: a robust Theil-Sen straight-line fit over the COMPLETE weeks only (the partial current week is dropped so it can't fake a dip) — read its slope for net direction, and the 4-wk rolling line for the actual shape. Cumulative: a constant-average-rate pace line — actual pulling above it = accelerating, flattening below = slowing.";
const TIP = {
  weekly:
    "Isolated weekly: the window is one week, so you need ~80–100 resolved deals that week to trust any single point at ±10. Most B2B funnels never hit that weekly at Stage 0→1. Below ~30/week the points are basically decorative — directional at best, and you should not react to a single week's movement. This is exactly why isolated weekly is the noisy option.",
  rolling4:
    "4-week rolling: the window pools 4 weeks, so the threshold is ~100 resolved deals across the trailing 4 weeks → roughly 25/week. This is the realistic sweet spot for most B2B volumes and the main reason rolling wins: it manufactures sample size you don't have weekly. At 25/week each point sits near ±10; at 40–50/week it tightens to ±7 or better.",
  cumulative:
    "Cumulative QTD: self-solving over time. Early weeks are garbage (week 1 = one week of data), but n grows every week, so by mid-quarter you're pooling 100+ almost regardless of weekly rate, and by quarter-end the interval is tight. The tradeoff isn't sample size, it's staleness — it's statistically accurate but increasingly insensitive to recent change.",
};
const SPEED_TIP =
  "For each week, the average days from Stage 0 to Stage 1 across deals that ENTERED Stage 1 that week, attributed to the deal's creator. Weekly = that week alone; 4-wk rolling and Cumulative pool the underlying deals (not averages of averages).";

export async function render(el) {
  const d = await api.prepipeline();
  const weeks = d.weeks;
  const groups = d.groups || [];

  el.innerHTML = `
    <div class="grid kpis">
      ${kpi("Stage-0 Created (Q2)", d.totals.stage0_created, "by createdate")}
      ${kpi("Creators", d.totals.creators_count, "incl. off-roster & archived")}
      ${kpi("Creation Trend", trendTag(d.created.trend), "over the quarter", true)}
    </div>

    <div class="panel">
      <h3>Weekly Stage-0 Created <span class="panel-sub">by creator · scoped to createdate in quarter</span></h3>
      <div class="mode-row">
        <div class="seg" id="pp-cr-mode">${["weekly", "rolling4", "cumulative"].map((m) =>
          `<button data-m="${m}"${m === "weekly" ? ' class="active"' : ""}>${MODE_LABEL[m]}</button>`).join("")}</div>
        <label class="check"><input type="checkbox" id="pp-cr-trend"/> Trend line ${info(TREND_TIP)}</label>
      </div>
      <div id="pp-cr-chips" class="chip-row"></div>
      ${groups.length ? `<div id="pp-cr-groups" class="chip-row group-row"></div>` : ""}
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

  buildCreated(d, weeks, groups);
  buildSpeed(d, weeks, groups);
  buildConversion(d, weeks, groups);
}

// --------------------------------------------------------------------------- //
// Section 1 — Stage-0 Created: mode selector + trend line + creator/group lines
// --------------------------------------------------------------------------- //
function buildCreated(d, weeks, groups) {
  const S = d.created.series;
  const state = { mode: "weekly", trend: false, visible: new Set(["agg"]) };

  const seriesVal = (s) => (state.mode === "cumulative" ? s.cumulative
    : state.mode === "rolling4" ? s.rolling4 : s.weekly);

  function draw() {
    const ds = [];
    const addLine = (key, label, data, col, width) => {
      if (!state.visible.has(key) || !data) return;
      ds.push({ label, data: data.map(r1), borderColor: col, backgroundColor: col,
        borderWidth: width, tension: 0.3, pointRadius: width >= 2.5 ? 2 : 0, spanGaps: true });
    };
    const addRef = (key, label, data, col) => {
      if (!state.trend || !state.visible.has(key) || !data) return;
      ds.push({ label, data: data.map(r1), borderColor: col, borderDash: [6, 4],
        borderWidth: 1.5, pointRadius: 0, tension: 0, spanGaps: true });
    };
    addLine("agg", "Aggregate", seriesVal(S.aggregate), AGG, 2.5);
    d.creators.forEach((c, i) => addLine(ck(c), c.first_name, seriesVal(S.by_creator[ck(c)]), color(i), 1.5));
    groups.forEach((g, gi) => addLine(gk(g), g.name, seriesVal(S.by_group[g.id]), gcolor(gi), 3));
    // reference line: fitted trend (weekly/rolling) or pace (cumulative), for aggregate + visible groups
    const refOf = (s) => (state.mode === "cumulative" ? s.pace : (s.fit ? s.fit.y : null));
    addRef("agg", "Aggregate " + (state.mode === "cumulative" ? "pace" : "trend"), refOf(S.aggregate), AGG);
    groups.forEach((g, gi) => addRef(gk(g), g.name + (state.mode === "cumulative" ? " pace" : " trend"), refOf(S.by_group[g.id]), gcolor(gi)));

    chart("pp-created", { type: "line", data: { labels: weeks, datasets: ds },
      options: { ...gridOpts, plugins: { legend: { display: false } } } });
  }

  // mode segmented control
  document.querySelectorAll("#pp-cr-mode button").forEach((b) => {
    b.onclick = () => {
      state.mode = b.dataset.m;
      document.querySelectorAll("#pp-cr-mode button").forEach((x) => x.classList.toggle("active", x === b));
      draw();
    };
  });
  document.getElementById("pp-cr-trend").onchange = (e) => { state.trend = e.target.checked; draw(); };

  // creator chips
  const chips = [{ key: "agg", label: "Aggregate", dot: AGG, title: "All creators combined" }]
    .concat(d.creators.map((c, i) => ({
      key: ck(c), label: c.first_name, dot: color(i),
      title: c.name + (c.role ? ` · ${c.role}` : "") + (c.archived ? " · archived" : "") + ` · ${c.total_created} created`,
    })));
  renderChips("pp-cr-chips", chips, state, draw);

  // group chips (distinct styling)
  if (groups.length) {
    const gchips = groups.map((g, gi) => ({
      key: gk(g), label: "▣ " + g.name, dot: gcolor(gi),
      title: g.member_names.join(", ") + ` · ${g.total_created} created`,
    }));
    renderChips("pp-cr-groups", gchips, state, draw);
  }

  draw();
  document.getElementById("pp-created-table").innerHTML = countTable(d, weeks, groups);
}

function renderChips(containerId, chips, state, draw) {
  document.getElementById(containerId).innerHTML = chips.map((c) =>
    `<button class="toggle-chip${state.visible.has(c.key) ? " active" : ""}" data-key="${esc(c.key)}" title="${esc(c.title)}">
       <span class="dot" style="background:${c.dot}"></span>${esc(c.label)}</button>`).join("");
  document.querySelectorAll(`#${containerId} .toggle-chip`).forEach((btn) => {
    btn.onclick = () => {
      const key = btn.dataset.key;
      if (state.visible.has(key)) state.visible.delete(key); else state.visible.add(key);
      btn.classList.toggle("active");
      draw();
    };
  });
}

// --------------------------------------------------------------------------- //
// Section 2 — Speed to S1 (chart unchanged; group rows added to the table)
// --------------------------------------------------------------------------- //
function buildSpeed(d, weeks, groups) {
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
  const ch = chart("pp-speed", { type: "line", data: { labels: weeks, datasets },
    options: { ...gridOpts, scales: { ...gridOpts.scales,
      y: { ...gridOpts.scales.y, ticks: { callback: (v) => v + "d" } } } } });
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
  document.getElementById("pp-speed-table").innerHTML = meanTable(d, weeks, groups);
}

// --------------------------------------------------------------------------- //
// Section 3 — Conversion (chart unchanged; group rows added to the table)
// --------------------------------------------------------------------------- //
function buildConversion(d, weeks, groups) {
  const sel = document.getElementById("pp-conv-mode");
  document.getElementById("pp-conv-info").innerHTML = info(TIP.weekly + "\n\n" + TIP.rolling4 + "\n\n" + TIP.cumulative);
  const draw = () => {
    const mode = sel.value;
    const series = d.conversion.aggregate[mode];
    chart("pp-conv", { type: "line",
      data: { labels: weeks, datasets: [{ label: "S0→S1 %",
        data: series.map((c) => (c.rate == null ? null : r1(c.rate * 100))),
        borderColor: PALETTE[0], backgroundColor: PALETTE[0], tension: 0.3, spanGaps: true, pointRadius: 2 }] },
      options: { ...gridOpts, scales: { ...gridOpts.scales,
        y: { ...gridOpts.scales.y, ticks: { callback: (v) => v + "%" } } } } });
    document.getElementById("pp-conv-caption").textContent = TIP[mode];
    document.getElementById("pp-conv-table").innerHTML = rateTable(d, weeks, mode, groups);
  };
  sel.onchange = draw;
  draw();
}

// --------------------------------------------------------------------------- //
// Tables (granular, per-creator; pinned pooled group rows)
// --------------------------------------------------------------------------- //
function header(weeks, extra = "") {
  return `<tr><th class="sticky">Creator</th>${weeks.map((w) => `<th class="num">${wlabel(w)}</th>`).join("")}${extra}</tr>`;
}
function nameCell(c) {
  return `<td class="sticky" title="${esc(c.name)}">${esc(c.first_name)}${c.archived ? ' <span class="muted">·arch</span>' : ""}</td>`;
}
function groupNameCell(g) {
  return `<td class="sticky" title="${esc(g.member_names.join(", "))}">▣ ${esc(g.name)}</td>`;
}

function countTable(d, weeks, groups) {
  const rows = [`<tr class="agg-row"><td class="sticky">Aggregate</td>${d.created.aggregate.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${d.totals.stage0_created}</td></tr>`];
  for (const g of groups) {
    const arr = d.created.by_group[g.id] || [];
    rows.push(`<tr class="group-pin">${groupNameCell(g)}${weeks.map((_, i) => `<td class="num">${arr[i] || "—"}</td>`).join("")}<td class="num">${g.total_created}</td></tr>`);
  }
  for (const c of d.creators) {
    const arr = d.created.by_creator[ck(c)] || [];
    rows.push(`<tr>${nameCell(c)}${weeks.map((_, i) => `<td class="num">${arr[i] || "—"}</td>`).join("")}<td class="num">${c.total_created}</td></tr>`);
  }
  return `<table class="grid-table">${header(weeks, '<th class="num">Total</th>')}${rows.join("")}</table>`;
}

function meanCell(c) {
  return c && c.n ? `${Math.round(c.avg * 10) / 10}<span class="muted"> (${c.n})</span>` : "—";
}
function meanTable(d, weeks, groups) {
  const rows = [`<tr class="agg-row"><td class="sticky">Aggregate</td>${d.speed.aggregate.weekly.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>`];
  for (const g of groups) {
    const s = (d.speed.by_group || {})[g.id];
    if (!s) continue;
    rows.push(`<tr class="group-pin">${groupNameCell(g)}${s.weekly.map((x) => `<td class="num">${meanCell(x)}</td>`).join("")}</tr>`);
  }
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
function rateTable(d, weeks, mode, groups) {
  const rows = [`<tr class="agg-row"><td class="sticky">Aggregate</td>${d.conversion.aggregate[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>`];
  for (const g of groups) {
    const s = (d.conversion.by_group || {})[g.id];
    if (!s) continue;
    rows.push(`<tr class="group-pin">${groupNameCell(g)}${s[mode].map((x) => `<td class="num">${rateCell(x)}</td>`).join("")}</tr>`);
  }
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
