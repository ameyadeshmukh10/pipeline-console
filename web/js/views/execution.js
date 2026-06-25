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
const STAGE_LABEL = { S1: "S1", S2: "S2", S3: "S3", S4: "S4", S5: "S5", WON: "CW", LOST: "CL" };
const TREND_TIP =
  "Robust Theil-Sen fit over complete weeks only (the partial current week is dropped); horizontal at the median when the net trend isn't significant.";
const CONV_TIP =
  "Isolated weekly is noisy below ~30 resolved deals/week. 4-week rolling pools ~4× the sample (the sweet spot). Cumulative QTD is statistically tight by mid-quarter but increasingly insensitive to recent change.";
const FUNNEL_NOTE =
  "How to read this: each gate is a cohort conversion. Of the deals that ENTERED a stage, what share advanced to the next stage — or beyond (a won deal counts as a success at every gate it cleared). Deals still sitting open at that stage are excluded: they haven't passed or failed the gate yet, so counting them would understate the rate. Multiply every gate together to get the full-funnel S0→Won conversion.";

let D, WEEKS, OWNERS, GROUPS, STATE, CTX;

export async function render(el, ctx) {
  CTX = ctx;
  D = await api.execution();
  WEEKS = D.weeks;
  OWNERS = D.owners;
  GROUPS = D.groups || [];
  const fs = D.funnel_summary;
  const et = D.entered_totals;

  STATE = {
    visible: new Set(["agg"]),
    s1s3: "median",
    entered: { stage: D.entered_stages[0], mode: "weekly", trend: false },
    velocity: { trans: D.transitions[0].key, stat: "median", mode: "weekly" },
    conversion: { gate: (D.gates.find((g) => g.key === "S1_S2") || D.gates[0]).key, mode: "rolling4" },
  };

  el.innerHTML = `
    <div class="cols-2">
      <div class="card stat-card">
        <div class="stat-head">Velocity S1→S3 <span class="muted" style="text-transform:none;font-weight:400">days</span>
          <span class="seg sm" id="ex-s1s3-stat"><button data-s="median" class="active">Median</button><button data-s="mean">Mean</button></span></div>
        <div id="ex-s1s3-body" class="stat-rows"></div>
      </div>
      <div class="card stat-card">
        <div class="stat-head">Deals Entered (Q2) <span class="muted" style="text-transform:none;font-weight:400">event-dated counts</span></div>
        <div class="stat-grid">
          ${["S1", "S2", "S3", "S4", "S5", "WON", "LOST"].map((s) =>
            `<div class="stat-cell"><div class="sv">${et[s] ?? 0}</div><div class="sl">${STAGE_LABEL[s]}</div></div>`).join("")}
        </div>
      </div>
    </div>

    <div class="panel funnel-panel">
      <h3>Funnel Conversion <span class="panel-sub">cohort conversion at each gate · open deals excluded</span></h3>
      <p class="muted small caption">${FUNNEL_NOTE}</p>
      ${funnelFlow(fs)}
    </div>

    <div class="panel">
      <h3>① Entered Stage <span class="panel-sub">weekly count of deals that entered the stage, by owner</span></h3>
      <div class="mode-row">
        ${selector("ex-stage", "Stage", D.entered_stages.map((s) => [s, s]))}
        <div class="seg" id="ex-entered-mode">${segBtns("weekly")}</div>
        <label class="check"><input type="checkbox" id="ex-entered-trend"/> Trend line ${info(TREND_TIP)}</label>
      </div>
      <div id="ex-chips" class="chip-row"></div>
      ${GROUPS.length ? `<div id="ex-gchips" class="chip-row group-row"></div>` : ""}
      <div class="chart-wrap"><canvas id="ex-entered"></canvas></div>
      <details class="more"><summary>Per-owner weekly counts</summary><div class="table-scroll" id="ex-entered-table"></div></details>
    </div>

    <div class="panel">
      <h3>② Stage Velocity <span class="panel-sub">days to advance to the next stage · aggregate + trend</span></h3>
      <div class="mode-row">
        ${selector("ex-trans", "Transition", D.transitions.map((t) => [t.key, t.label]))}
        <div class="seg" id="ex-vel-stat"><button data-s="median" class="active">Median</button><button data-s="mean">Mean</button></div>
        <div class="seg" id="ex-vel-mode">${segBtns("weekly")}</div>
      </div>
      <div class="chart-wrap"><canvas id="ex-vel"></canvas></div>
      <details class="more" open><summary>Per-owner weekly value (n)</summary><div class="table-scroll" id="ex-vel-table"></div></details>
    </div>

    <div class="panel">
      <h3>③ Funnel Conversion (weekly) <span class="panel-sub">cohort by week entered the stage · aggregate + trend</span></h3>
      <div class="mode-row">
        ${selector("ex-gate", "Gate", D.gates.map((g) => [g.key, g.label]))}
        <div class="seg" id="ex-conv-mode">${segBtns("rolling4")}</div>
        ${info(CONV_TIP)}
      </div>
      <div class="chart-wrap"><canvas id="ex-conv"></canvas></div>
      <details class="more"><summary>Per-owner conversion (denominator)</summary><div class="table-scroll" id="ex-conv-table"></div></details>
    </div>

    <div class="panel">
      <h3>Open Pipeline Aging <span class="panel-sub">open deals over target with no resolution · click a row to inspect</span></h3>
      <div class="flag-list" style="margin-bottom:12px">${Object.entries(D.aging.summary).map(([s, o]) => `<span class="pill">${s}: ${o.aging}/${o.open} aging · med ${fmt.days(o.median_age)}</span>`).join("")}</div>
      <div class="table-scroll"><table><thead><tr><th>Deal</th><th>Stage</th><th>Owner</th><th class="num">Age in stage</th></tr></thead>
        <tbody>${D.aging.deals.slice(0, 40).map((r) => `<tr class="row-click" data-deal="${esc(r.deal_id)}"><td>${esc(r.dealname || "—")}</td><td>${r.stage}</td><td>${esc(r.owner)}</td><td class="num">${fmt.days(r.age_days)}</td></tr>`).join("")}</tbody></table></div>
    </div>`;

  el.querySelectorAll("[data-deal]").forEach((tr) => { tr.onclick = () => CTX.openInspector(tr.dataset.deal); });

  renderS1S3();
  document.querySelectorAll("#ex-s1s3-stat button").forEach((b) => b.onclick = () => {
    STATE.s1s3 = b.dataset.s;
    document.querySelectorAll("#ex-s1s3-stat button").forEach((x) => x.classList.toggle("active", x === b));
    renderS1S3();
  });

  buildChips();
  wireEntered();
  wireVelocity();
  wireConversion();
  drawEntered();
  drawVelocity();
  drawConversion();
}

function renderS1S3() {
  const v = D.velocity_s1_s3, st = STATE.s1s3;
  const row = (lbl, k) => { const c = v[k]; return `<div class="stat-row"><span class="lbl">${lbl}</span><span class="val">${c[st] == null ? "—" : r1(c[st]) + "d"} <span class="n">(${c.n})</span></span></div>`; };
  document.getElementById("ex-s1s3-body").innerHTML =
    row("Full Quarter", "full") + row("First Third", "first_third") + row("Second Third", "second_third") + row("Last Third", "last_third");
}

function funnelFlow(fs) {
  const cards = fs.gates.map((g) =>
    `<div class="gate-card"><div class="gate-label">${g.label}</div><div class="gate-rate">${g.rate == null ? "—" : Math.round(g.rate * 100) + "%"}</div><div class="gate-sub">${g.advanced}/${g.denom}</div></div>`
  ).join('<div class="funnel-arrow">→</div>');
  const full = `<div class="gate-card full"><div class="gate-label">S0→Won</div><div class="gate-rate">${fs.full_funnel == null ? "—" : (fs.full_funnel * 100).toFixed(1) + "%"}</div><div class="gate-sub">full funnel</div></div>`;
  return `<div class="funnel-flow">${cards}<div class="funnel-arrow">=</div>${full}</div>`;
}

// --- owner/group chips (drive the Entered chart) ------------------------------
function buildChips() {
  const chips = [{ key: "agg", label: "Aggregate", dot: AGG, title: "All owners combined" }]
    .concat(OWNERS.map((o, i) => ({ key: okey(o), label: o.first_name, dot: color(i), title: o.name + (o.archived ? " · archived" : "") + ` · ${o.total_created} S1+ deals` })));
  renderChips("ex-chips", chips);
  if (GROUPS.length) renderChips("ex-gchips", GROUPS.map((g, i) => ({ key: gkey(g), label: "▣ " + g.name, dot: gcolor(i), title: g.member_names.join(", ") })));
}
function renderChips(id, chips) {
  document.getElementById(id).innerHTML = chips.map((c) =>
    `<button class="toggle-chip${STATE.visible.has(c.key) ? " active" : ""}" data-key="${esc(c.key)}" title="${esc(c.title)}"><span class="dot" style="background:${c.dot}"></span>${esc(c.label)}</button>`).join("");
  document.querySelectorAll(`#${id} .toggle-chip`).forEach((b) => b.onclick = () => {
    const k = b.dataset.key;
    STATE.visible.has(k) ? STATE.visible.delete(k) : STATE.visible.add(k);
    b.classList.toggle("active");
    drawEntered();
  });
}
function visibleSeries() {
  const out = [];
  if (STATE.visible.has("agg")) out.push({ key: "agg", label: "Aggregate", c: AGG, w: 2.5 });
  OWNERS.forEach((o, i) => { const k = okey(o); if (STATE.visible.has(k)) out.push({ key: k, label: o.first_name, c: color(i), w: 1.5 }); });
  GROUPS.forEach((g, i) => { const k = gkey(g); if (STATE.visible.has(k)) out.push({ key: k, label: g.name, c: gcolor(i), w: 3 }); });
  return out;
}
function lineCfg(datasets, yTick) {
  const y = { ...gridOpts.scales.y };
  if (yTick) y.ticks = { callback: yTick };
  return { type: "line", data: { labels: WEEKS, datasets }, options: { ...gridOpts, plugins: { legend: { display: false } }, scales: { ...gridOpts.scales, y } } };
}
function trendDs(fit) {
  return fit ? { label: "Trend", data: fit.y.map(r1), borderColor: AGG, borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, tension: 0, spanGaps: true } : null;
}

// --- ① Entered (multi-owner) -------------------------------------------------
function wireEntered() {
  sel("ex-stage", (v) => { STATE.entered.stage = v; drawEntered(); });
  seg("ex-entered-mode", (m) => { STATE.entered.mode = m; drawEntered(); });
  document.getElementById("ex-entered-trend").onchange = (e) => { STATE.entered.trend = e.target.checked; drawEntered(); };
}
function drawEntered() {
  const s = STATE.entered, E = D.entered[s.stage];
  const pick = (so) => (s.mode === "cumulative" ? so.cumulative : s.mode === "rolling4" ? so.rolling4 : so.weekly);
  const so = (key) => key === "agg" ? E.series.aggregate : key.startsWith("g:") ? E.series.by_group[key.slice(2)] : E.series.by_owner[key];
  const ds = visibleSeries().map((x) => { const o = so(x.key); return { label: x.label, data: o ? pick(o).map(r1) : [], borderColor: x.c, backgroundColor: x.c, borderWidth: x.w, tension: 0.3, pointRadius: x.w >= 2.5 ? 2 : 0, spanGaps: true }; });
  if (s.trend) {
    visibleSeries().filter((x) => x.key === "agg" || x.key.startsWith("g:")).forEach((x) => {
      const o = so(x.key); if (!o) return;
      const y = s.mode === "cumulative" ? o.pace : (o.fit ? o.fit.y : null);
      if (y) ds.push({ label: x.label + " trend", data: y.map(r1), borderColor: x.c, borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, tension: 0, spanGaps: true });
    });
  }
  chart("ex-entered", lineCfg(ds));
  document.getElementById("ex-entered-table").innerHTML = countTable(E);
}

// --- ② Velocity (aggregate + trend) ------------------------------------------
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
  const s = STATE.velocity, V = D.velocity[s.trans], A = V.aggregate[s.stat];
  const ds = [{ label: "Aggregate", data: A[s.mode].map((c) => r1(c.avg)), borderColor: AGG, backgroundColor: AGG, borderWidth: 2.5, tension: 0.3, pointRadius: 2, spanGaps: true }];
  const t = trendDs(A.fit); if (t) ds.push(t);
  chart("ex-vel", lineCfg(ds, (v) => v + "d"));
  document.getElementById("ex-vel-table").innerHTML = velTable(V, s.stat);
}

// --- ③ Conversion (aggregate + trend) ----------------------------------------
function wireConversion() {
  sel("ex-gate", (v) => { STATE.conversion.gate = v; drawConversion(); });
  seg("ex-conv-mode", (m) => { STATE.conversion.mode = m; drawConversion(); });
}
function drawConversion() {
  const s = STATE.conversion, C = D.conversion[s.gate];
  const ds = [{ label: "S0→S1 %", data: C.aggregate[s.mode].map((c) => (c.rate == null ? null : r1(c.rate * 100))), borderColor: AGG, backgroundColor: AGG, borderWidth: 2.5, tension: 0.3, pointRadius: 2, spanGaps: true }];
  const t = trendDs(C.aggregate.fit); if (t) ds.push(t);
  chart("ex-conv", lineCfg(ds, (v) => v + "%"));
  document.getElementById("ex-conv-table").innerHTML = convTable(C, s.mode);
}

// --- tables ------------------------------------------------------------------
function header(extra = "") { return `<tr><th class="sticky">Owner</th>${WEEKS.map((w) => `<th class="num">${wlabel(w)}</th>`).join("")}${extra}</tr>`; }
function nameCell(o) { return `<td class="sticky" title="${esc(o.name)}">${esc(o.first_name)}${o.archived ? ' <span class="muted">·arch</span>' : ""}</td>`; }
function gNameCell(g) { return `<td class="sticky" title="${esc(g.member_names.join(", "))}">▣ ${esc(g.name)}</td>`; }
function rows(aggRow, groupRow, ownerRow) {
  const out = [aggRow];
  for (const g of GROUPS) { const r = groupRow(g); if (r) out.push(r); }
  for (const o of OWNERS) { const r = ownerRow(o); if (r) out.push(r); }
  return out.join("");
}
function countTable(E) {
  const total = E.aggregate.reduce((a, b) => a + b, 0);
  const agg = `<tr class="agg-row"><td class="sticky">Aggregate</td>${E.aggregate.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${total}</td></tr>`;
  return `<table class="grid-table">${header('<th class="num">Total</th>')}${rows(agg,
    (g) => { const a = E.by_group[g.id]; return a ? `<tr class="group-pin">${gNameCell(g)}${a.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${a.reduce((x, y) => x + y, 0)}</td></tr>` : null; },
    (o) => { const a = E.by_owner[okey(o)]; return a ? `<tr>${nameCell(o)}${a.map((v) => `<td class="num">${v || "—"}</td>`).join("")}<td class="num">${a.reduce((x, y) => x + y, 0)}</td></tr>` : null; })}</table>`;
}
function meanCell(c) { return c && c.n ? `${Math.round(c.avg * 10) / 10}<span class="muted"> (${c.n})</span>` : "—"; }
function velTable(V, stat) {
  const agg = `<tr class="agg-row"><td class="sticky">Aggregate</td>${V.aggregate[stat].weekly.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>`;
  return `<table class="grid-table">${header()}${rows(agg,
    (g) => { const o = V.by_group[g.id]; return o ? `<tr class="group-pin">${gNameCell(g)}${o[stat].weekly.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>` : null; },
    (o) => { const x = V.by_owner[okey(o)]; return x ? `<tr>${nameCell(o)}${x[stat].weekly.map((c) => `<td class="num">${meanCell(c)}</td>`).join("")}</tr>` : null; })}</table>`;
}
function rateCell(c) { return c && c.denom ? `${Math.round(c.rate * 100)}%<span class="muted"> (${c.denom})</span>` : "—"; }
function convTable(C, mode) {
  const agg = `<tr class="agg-row"><td class="sticky">Aggregate</td>${C.aggregate[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>`;
  return `<table class="grid-table">${header()}${rows(agg,
    (g) => { const o = C.by_group[g.id]; return o ? `<tr class="group-pin">${gNameCell(g)}${o[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>` : null; },
    (o) => { const x = C.by_owner[okey(o)]; return x ? `<tr>${nameCell(o)}${x[mode].map((c) => `<td class="num">${rateCell(c)}</td>`).join("")}</tr>` : null; })}</table>`;
}

// --- small control helpers ---------------------------------------------------
function selector(id, label, opts) { return `<label class="ex-sel">${label} <select id="${id}">${opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select></label>`; }
function segBtns(active) { return MODE.map(([m, l]) => `<button data-m="${m}"${m === active ? ' class="active"' : ""}>${l}</button>`).join(""); }
function sel(id, fn) { document.getElementById(id).onchange = (e) => fn(e.target.value); }
function seg(id, fn) {
  document.querySelectorAll(`#${id} button`).forEach((b) => b.onclick = () => {
    document.querySelectorAll(`#${id} button`).forEach((x) => x.classList.toggle("active", x === b));
    fn(b.dataset.m);
  });
}
