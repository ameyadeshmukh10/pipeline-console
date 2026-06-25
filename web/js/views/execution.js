import { api } from "../api.js";
import { esc, info, fmt } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

const AGG = "#11203a";
const GPAL = ["#7c3aed", "#0e7490", "#b91c1c", "#15803d", "#a16207", "#9d174d", "#1e3a8a", "#9333ea"];
const r1 = (v) => (v == null ? null : Math.round(v * 10) / 10);
const okey = (o) => o.id || "unassigned";
const gkey = (g) => "g:" + g.id;
const color = (i) => PALETTE[i % PALETTE.length];
const gcolor = (i) => GPAL[i % GPAL.length];
const wlabel = (w) => w.replace(/^\d{4}-/, "");

const MODE = [["weekly", "Weekly"], ["rolling4", "4-wk rolling"], ["cumulative", "Cumulative"]];
const TREND_TIP =
  "Robust Theil-Sen fit over complete weeks only (the partial current week is dropped); horizontal at the median when the net trend isn't significant. Cumulative shows a constant-rate pace line instead.";
const CONV_TIP =
  "Isolated weekly is noisy below ~30 resolved deals/week. 4-week rolling pools ~4× the sample (the sweet spot). Cumulative QTD is statistically tight by mid-quarter but increasingly insensitive to recent change.";

let D, WEEKS, OWNERS, GROUPS, STATE;

export async function render(el) {
  D = await api.execution();
  WEEKS = D.weeks;
  OWNERS = D.owners;
  GROUPS = D.groups || [];
  const fs = D.funnel_summary;
  const winGate = fs.gates.find((g) => g.key === "S5_WON");

  STATE = {
    visible: new Set(["agg"]),
    entered: { stage: D.entered_stages[0], mode: "weekly", trend: false },
    velocity: { trans: D.transitions[0].key, stat: "median", mode: "weekly" },
    conversion: { gate: (D.gates.find((g) => g.key === "S1_S2") || D.gates[0]).key, mode: "rolling4" },
  };

  el.innerHTML = `
    <div class="grid kpis">
      ${kpi("S1+ Owners (Q2)", D.totals.owners, "everyone working the pipeline")}
      ${kpi("Full Funnel S0→Won", fs.full_funnel == null ? "—" : (fs.full_funnel * 100).toFixed(1) + "%", "Π of all gate rates")}
      ${kpi("Win Rate (S5→Won)", winGate && winGate.rate != null ? (winGate.rate * 100).toFixed(0) + "%" : "—", winGate ? `n=${winGate.denom}` : "")}
    </div>

    <div class="panel filter-panel">
      <div class="panel-sub" style="margin-bottom:6px">Owners <span class="muted">— toggle to show on every chart below</span></div>
      <div id="ex-chips" class="chip-row"></div>
      ${GROUPS.length ? `<div id="ex-gchips" class="chip-row group-row"></div>` : ""}
    </div>

    <div class="panel">
      <h3>① Entered Stage <span class="panel-sub">weekly count of deals that entered the stage, by owner</span></h3>
      <div class="mode-row">
        ${selector("ex-stage", "Stage", D.entered_stages.map((s) => [s, s]))}
        <div class="seg" id="ex-entered-mode">${segBtns("weekly")}</div>
        <label class="check"><input type="checkbox" id="ex-entered-trend"/> Trend line ${info(TREND_TIP)}</label>
      </div>
      <div class="chart-wrap"><canvas id="ex-entered"></canvas></div>
      <details class="more"><summary>Per-owner weekly counts</summary><div class="table-scroll" id="ex-entered-table"></div></details>
    </div>

    <div class="panel">
      <h3>② Stage Velocity <span class="panel-sub">days to advance to the next stage, by owner</span></h3>
      <div class="mode-row">
        ${selector("ex-trans", "Transition", D.transitions.map((t) => [t.key, t.label]))}
        <div class="seg" id="ex-vel-stat"><button data-s="median" class="active">Median</button><button data-s="mean">Mean</button></div>
        <div class="seg" id="ex-vel-mode">${segBtns("weekly")}</div>
      </div>
      <div class="chart-wrap"><canvas id="ex-vel"></canvas></div>
      <details class="more" open><summary>Per-owner weekly value (n)</summary><div class="table-scroll" id="ex-vel-table"></div></details>
    </div>

    <div class="panel">
      <h3>③ Funnel Conversion <span class="panel-sub">reached N+1-or-beyond ÷ (reached + closed-lost-at-N)</span></h3>
      <div class="mode-row">
        ${selector("ex-gate", "Gate", D.gates.map((g) => [g.key, g.label]))}
        <div class="seg" id="ex-conv-mode">${segBtns("rolling4")}</div>
        ${info(CONV_TIP)}
      </div>
      <div class="chart-wrap"><canvas id="ex-conv"></canvas></div>
      <details class="more"><summary>Per-owner conversion (denominator)</summary><div class="table-scroll" id="ex-conv-table"></div></details>
    </div>

    <div class="panel">
      <h3>Funnel Summary <span class="panel-sub">QTD conversion at each gate · full-funnel = product</span></h3>
      <div class="table-scroll">${funnelTable(fs)}</div>
    </div>

    <div class="panel">
      <h3>Open Pipeline Aging <span class="panel-sub">open deals over target with no resolution</span></h3>
      <div class="flag-list" style="margin-bottom:12px">${Object.entries(D.aging.summary).map(([s, o]) => `<span class="pill">${s}: ${o.aging}/${o.open} aging · med ${fmt.days(o.median_age)}</span>`).join("")}</div>
      <div class="table-scroll"><table><thead><tr><th>Deal</th><th>Stage</th><th>Owner</th><th class="num">Age in stage</th></tr></thead>
        <tbody>${D.aging.deals.slice(0, 40).map((r) => `<tr>${r.dealname ? `<td>${esc(r.dealname)}</td>` : "<td>—</td>"}<td>${r.stage}</td><td>${esc(r.owner)}</td><td class="num">${fmt.days(r.age_days)}</td></tr>`).join("")}</tbody></table></div>
    </div>`;

  buildChips();
  wireEntered();
  wireVelocity();
  wireConversion();
  drawEntered();
  drawVelocity();
  drawConversion();
}

// --- shared owner/group filter ------------------------------------------------
function buildChips() {
  const chips = [{ key: "agg", label: "Aggregate", dot: AGG, title: "All owners combined" }]
    .concat(OWNERS.map((o, i) => ({
      key: okey(o), label: o.first_name, dot: color(i),
      title: o.name + (o.archived ? " · archived" : "") + ` · ${o.total_created} S1+ deals`,
    })));
  renderChips("ex-chips", chips);
  if (GROUPS.length) {
    renderChips("ex-gchips", GROUPS.map((g, i) => ({
      key: gkey(g), label: "▣ " + g.name, dot: gcolor(i), title: g.member_names.join(", "),
    })));
  }
}
function renderChips(id, chips) {
  document.getElementById(id).innerHTML = chips.map((c) =>
    `<button class="toggle-chip${STATE.visible.has(c.key) ? " active" : ""}" data-key="${esc(c.key)}" title="${esc(c.title)}">
       <span class="dot" style="background:${c.dot}"></span>${esc(c.label)}</button>`).join("");
  document.querySelectorAll(`#${id} .toggle-chip`).forEach((b) => {
    b.onclick = () => {
      const k = b.dataset.key;
      STATE.visible.has(k) ? STATE.visible.delete(k) : STATE.visible.add(k);
      b.classList.toggle("active");
      drawEntered(); drawVelocity(); drawConversion();
    };
  });
}

function visibleSeries() {
  const out = [];
  if (STATE.visible.has("agg")) out.push({ key: "agg", label: "Aggregate", c: AGG, w: 2.5 });
  OWNERS.forEach((o, i) => { const k = okey(o); if (STATE.visible.has(k)) out.push({ key: k, label: o.first_name, c: color(i), w: 1.5 }); });
  GROUPS.forEach((g, i) => { const k = gkey(g); if (STATE.visible.has(k)) out.push({ key: k, label: g.name, c: gcolor(i), w: 3 }); });
  return out;
}
function lines(valueFor) {
  return visibleSeries().map((s) => ({
    label: s.label, data: valueFor(s.key), borderColor: s.c, backgroundColor: s.c,
    borderWidth: s.w, tension: 0.3, pointRadius: s.w >= 2.5 ? 2 : 0, spanGaps: true,
  }));
}
function refs(refFor) {
  return visibleSeries().filter((s) => s.key === "agg" || s.key.startsWith("g:")).map((s) => {
    const y = refFor(s.key);
    return y ? { label: s.label + " trend", data: y.map(r1), borderColor: s.c, borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, tension: 0, spanGaps: true } : null;
  }).filter(Boolean);
}
function lineCfg(datasets, yTick) {
  const y = { ...gridOpts.scales.y };
  if (yTick) y.ticks = { callback: yTick };
  return { type: "line", data: { labels: WEEKS, datasets }, options: { ...gridOpts, plugins: { legend: { display: false } }, scales: { ...gridOpts.scales, y } } };
}

// --- ① Entered ---------------------------------------------------------------
function wireEntered() {
  sel("ex-stage", (v) => { STATE.entered.stage = v; drawEntered(); });
  seg("ex-entered-mode", (m) => { STATE.entered.mode = m; drawEntered(); });
  document.getElementById("ex-entered-trend").onchange = (e) => { STATE.entered.trend = e.target.checked; drawEntered(); };
}
function drawEntered() {
  const s = STATE.entered, E = D.entered[s.stage];
  const pick = (so) => (s.mode === "cumulative" ? so.cumulative : s.mode === "rolling4" ? so.rolling4 : so.weekly);
  const so = (key) => key === "agg" ? E.series.aggregate : key.startsWith("g:") ? E.series.by_group[key.slice(2)] : E.series.by_owner[key];
  const ds = lines((key) => { const x = so(key); return x ? pick(x).map(r1) : []; });
  if (s.trend) ds.push(...refs((key) => { const x = so(key); if (!x) return null; return s.mode === "cumulative" ? x.pace : (x.fit ? x.fit.y : null); }));
  chart("ex-entered", lineCfg(ds));
  document.getElementById("ex-entered-table").innerHTML = countTable(E);
}

// --- ② Velocity --------------------------------------------------------------
function wireVelocity() {
  sel("ex-trans", (v) => { STATE.velocity.trans = v; drawVelocity(); });
  seg("ex-vel-mode", (m) => { STATE.velocity.mode = m; drawVelocity(); });
  document.querySelectorAll("#ex-vel-stat button").forEach((b) => b.onclick = () => {
    STATE.velocity.stat = b.dataset.s;
    document.querySelectorAll("#ex-vel-stat button").forEach((x) => x.classList.toggle("active", x === b));
    drawVelocity();
  });
}
function drawVelocity() {
  const s = STATE.velocity, V = D.velocity[s.trans];
  const cells = (key) => {
    const o = key === "agg" ? V.aggregate : key.startsWith("g:") ? V.by_group[key.slice(2)] : V.by_owner[key];
    if (!o) return null;
    return o[s.stat][s.mode];
  };
  const ds = lines((key) => { const c = cells(key); return c ? c.map((x) => r1(x.avg)) : []; });
  chart("ex-vel", lineCfg(ds, (v) => v + "d"));
  document.getElementById("ex-vel-table").innerHTML = velTable(V, s.stat);
}

// --- ③ Conversion ------------------------------------------------------------
function wireConversion() {
  sel("ex-gate", (v) => { STATE.conversion.gate = v; drawConversion(); });
  seg("ex-conv-mode", (m) => { STATE.conversion.mode = m; drawConversion(); });
}
function drawConversion() {
  const s = STATE.conversion, C = D.conversion[s.gate];
  const cells = (key) => {
    const o = key === "agg" ? C.aggregate : key.startsWith("g:") ? C.by_group[key.slice(2)] : C.by_owner[key];
    return o ? o[s.mode] : null;
  };
  const ds = lines((key) => { const c = cells(key); return c ? c.map((x) => (x.rate == null ? null : r1(x.rate * 100))) : []; });
  chart("ex-conv", lineCfg(ds, (v) => v + "%"));
  document.getElementById("ex-conv-table").innerHTML = convTable(C, s.mode);
}

// --- tables ------------------------------------------------------------------
function header(extra = "") {
  return `<tr><th class="sticky">Owner</th>${WEEKS.map((w) => `<th class="num">${wlabel(w)}</th>`).join("")}${extra}</tr>`;
}
function nameCell(o) {
  return `<td class="sticky" title="${esc(o.name)}">${esc(o.first_name)}${o.archived ? ' <span class="muted">·arch</span>' : ""}</td>`;
}
function gNameCell(g) {
  return `<td class="sticky" title="${esc(g.member_names.join(", "))}">▣ ${esc(g.name)}</td>`;
}
function rows(aggRow, groupRow, ownerRow) {
  const out = [aggRow];
  for (const g of GROUPS) { const r = groupRow(g); if (r) out.push(r); }
  for (const o of OWNERS) { const r = ownerRow(o); if (r) out.push(r); }
  return out.join("");
}
function countTable(E) {
  const total = E.aggregate.reduce((a, b) => a + b, 0);
  const agg = `<tr class="agg-row"><td class="sticky">Aggregate</td>${E.aggregate.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${total}</td></tr>`;
  const body = rows(agg,
    (g) => { const a = E.by_group[g.id]; return a ? `<tr class="group-pin">${gNameCell(g)}${a.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${a.reduce((x, y) => x + y, 0)}</td></tr>` : null; },
    (o) => { const a = E.by_owner[okey(o)]; return a ? `<tr>${nameCell(o)}${a.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${a.reduce((x, y) => x + y, 0)}</td></tr>` : null; });
  return `<table class="grid-table">${header('<th class="num">Total</th>')}${body}</table>`;
}
function meanCell(c) { return c && c.n ? `${Math.round(c.avg * 10) / 10}<span class="muted"> (${c.n})</span>` : "—"; }
function velTable(V, stat) {
  const a = V.aggregate[stat].weekly;
  const agg = `<tr class="agg-row"><td class="sticky">Aggregate</td>${a.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>`;
  const body = rows(agg,
    (g) => { const o = V.by_group[g.id]; return o ? `<tr class="group-pin">${gNameCell(g)}${o[stat].weekly.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>` : null; },
    (o) => { const x = V.by_owner[okey(o)]; return x ? `<tr>${nameCell(o)}${x[stat].weekly.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>` : null; });
  return `<table class="grid-table">${header()}${body}</table>`;
}
function rateCell(c) { return c && c.denom ? `${Math.round(c.rate * 100)}%<span class="muted"> (${c.denom})</span>` : "—"; }
function convTable(C, mode) {
  const agg = `<tr class="agg-row"><td class="sticky">Aggregate</td>${C.aggregate[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>`;
  const body = rows(agg,
    (g) => { const o = C.by_group[g.id]; return o ? `<tr class="group-pin">${gNameCell(g)}${o[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>` : null; },
    (o) => { const x = C.by_owner[okey(o)]; return x ? `<tr>${nameCell(o)}${x[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>` : null; });
  return `<table class="grid-table">${header()}${body}</table>`;
}
function funnelTable(fs) {
  const head = `<tr><th>Gate</th>${fs.gates.map((g) => `<th class="num">${g.label}</th>`).join("")}<th class="num">Full funnel</th></tr>`;
  const rate = `<tr class="agg-row"><td class="sticky">Conversion</td>${fs.gates.map((g) => `<td class="num">${g.rate == null ? "—" : Math.round(g.rate * 100) + "%"}</td>`).join("")}<td class="num">${fs.full_funnel == null ? "—" : (fs.full_funnel * 100).toFixed(1) + "%"}</td></tr>`;
  const den = `<tr><td class="sticky muted">resolved (n)</td>${fs.gates.map((g) => `<td class="num muted">${g.denom}</td>`).join("")}<td></td></tr>`;
  return `<table class="grid-table">${head}${rate}${den}</table>`;
}

// --- small control helpers ---------------------------------------------------
function selector(id, label, opts) {
  return `<label class="ex-sel">${label} <select id="${id}">${opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></label>`;
}
function segBtns(active) {
  return MODE.map(([m, l]) => `<button data-m="${m}"${m === active ? ' class="active"' : ""}>${l}</button>`).join("");
}
function sel(id, fn) { document.getElementById(id).onchange = (e) => fn(e.target.value); }
function seg(id, fn) {
  document.querySelectorAll(`#${id} button`).forEach((b) => b.onclick = () => {
    document.querySelectorAll(`#${id} button`).forEach((x) => x.classList.toggle("active", x === b));
    fn(b.dataset.m);
  });
}
function kpi(label, value, sub) {
  return `<div class="card kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${esc(value)}</div><div class="kpi-sub">${sub}</div></div>`;
}
