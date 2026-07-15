import { api } from "../api.js";
import { fmt, esc, info } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

const TIP = {
  commit: "Banked Closed-Won inside the analysis window + late-stage deals (S4/S5) you are actively working (engaged within the activity window).",
  most_likely: "Closed-Won + every open S1–S5 deal weighted by its stage win-probability. The expected value of the pipeline.",
  best_case: "Closed-Won + early pipeline (S1/S2) at weighted value + every Proposal-stage-and-later deal (S3+) landing at full value. The realistic ceiling.",
  coverage: "Open pipeline ÷ quarter target. ≥3× healthy, 2–3× watch, <2× thin.",
  scenario: "Which bucket each deal falls in: Commit = active S4/S5; Best = S3+ (or stalled late-stage); Upside = early S1/S2. Every deal still contributes to Most-likely (weighted).",
};

const state = { ownerId: null, target: "" };

export async function render(el, ctx) {
  const { targets } = await api.forecastTargets();
  if (!targets.length) { el.innerHTML = `<div class="empty">No open S1–S5 pipeline. Run a sync first.</div>`; return; }
  if (!state.ownerId || !targets.find((t) => t.owner_id === state.ownerId))
    state.ownerId = targets.find((t) => t.in_roster)?.owner_id || targets[0].owner_id;

  el.innerHTML = `
    <div class="panel">
      <div class="toolbar">
        <label class="field">Account Executive
          <select id="fc-ae">${targets.map((t) => `<option value="${t.owner_id}" ${t.owner_id === state.ownerId ? "selected" : ""}>${esc(t.name)} · ${t.open_deals} open · ${fmt.money(t.open_amount)}</option>`).join("")}</select></label>
        <label class="field">Quarter target ($)
          <input id="fc-target" type="number" placeholder="from settings" value="${state.target}" style="width:150px" /></label>
        <button id="fc-run" class="btn-primary" style="margin-left:auto">⚡ Run forecast agent</button>
      </div>
    </div>
    <div id="fc-body"><div class="loading">Loading…</div></div>`;

  el.querySelector("#fc-ae").onchange = (e) => { state.ownerId = e.target.value; load(el, ctx); };
  el.querySelector("#fc-target").onchange = (e) => { state.target = e.target.value; load(el, ctx); };
  el.querySelector("#fc-run").onclick = () => runAgent(el, ctx);
  await load(el, ctx);
}

async function load(el, ctx) {
  const body = el.querySelector("#fc-body");
  body.innerHTML = `<div class="loading">Loading…</div>`;
  const d = await api.forecast(state.ownerId, state.target || undefined);
  const p = d.packet, sc = p.scenarios, cov = p.coverage;
  body.innerHTML = `
    <div class="grid kpis">
      ${kpi("Commit" + info(TIP.commit), fmt.money(sc.commit), "won + active S4/S5", null, true)}
      ${kpi("Most-Likely" + info(TIP.most_likely), fmt.money(sc.most_likely), "probability-weighted", "good", true)}
      ${kpi("Best Case" + info(TIP.best_case), fmt.money(sc.best_case), "all S3+ lands at full value", null, true)}
      ${kpi("Coverage" + info(TIP.coverage), cov.pipeline_coverage != null ? cov.pipeline_coverage + "×" : "—", cov.gap_to_target != null ? "gap " + fmt.money(cov.gap_to_target) : "set a target", healthCls(cov.health), true)}
    </div>
    <div class="cols-2">
      <div class="panel"><h3>Scenarios <span class="panel-sub">${esc(p.ae.name)} · open pipeline ${fmt.money(cov.open_pipeline)}${p.ae.target ? " · target " + fmt.money(p.ae.target) : ""}</span></h3>
        <div class="chart-wrap"><canvas id="fc-scen"></canvas></div></div>
      <div class="panel"><h3>Agent Analysis ${d.last_run ? `<span class="panel-sub">${esc(d.last_run.model || "")} · ${fmt.ago(d.last_run.created_at)}</span>` : ""}</h3>
        <div id="fc-narr">${narrative(d.last_run ? d.last_run.narrative : null, p)}</div></div>
    </div>
    <div class="panel"><h3>Open Deals <span class="panel-sub">${p.deals.length} deals · weighted = amount × stage probability (${p.probability_source})</span></h3>
      <div class="table-scroll"><table><thead><tr><th>Deal</th><th>Stage</th><th>Scenario${info(TIP.scenario)}</th><th class="num">Amount</th><th class="num">p</th><th class="num">Weighted</th><th class="num">Close</th><th class="num">Days in stage</th><th class="num">Last activity</th><th>Flags</th></tr></thead>
        <tbody>${p.deals.map(dealRow).join("")}</tbody></table></div></div>`;

  body.querySelectorAll("[data-deal]").forEach((tr) => tr.onclick = () => ctx.openInspector(tr.dataset.deal));
  body.querySelectorAll("[data-ins]").forEach((s) => s.onclick = () => ctx.openInspector(s.dataset.ins));

  chart("fc-scen", {
    type: "bar",
    data: { labels: ["Worst", "Commit", "Most-likely", "Best"],
      datasets: [{ data: [sc.worst_case, sc.commit, sc.most_likely, sc.best_case],
        backgroundColor: ["#e0455b", "#e08a1e", "#4f6ef7", "#1faa6b"] }] },
    options: { ...gridOpts, plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => fmt.money(c.raw) } } },
      scales: { x: { grid: { display: false } }, y: { beginAtZero: true, grid: { color: "#eef0f4" }, ticks: { callback: (v) => fmt.money(v) } } } },
  });
}

async function runAgent(el, ctx) {
  const btn = el.querySelector("#fc-run");
  btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> Running…`;
  try {
    const r = await api.forecastRun(state.ownerId, state.target ? +state.target : undefined);
    const narr = el.querySelector("#fc-narr");
    if (narr) {
      narr.innerHTML = narrative(r.narrative, r.packet);
      narr.querySelectorAll("[data-ins]").forEach((s) => s.onclick = () => ctx.openInspector(s.dataset.ins));
    }
  } catch (e) {
    alert("Agent run failed: " + e.message);
  } finally {
    btn.disabled = false; btn.innerHTML = "⚡ Run forecast agent";
  }
}

function narrative(n, packet) {
  if (!n) return `<div class="muted small">No agent run yet. Click “Run forecast agent”.</div>`;
  const names = Object.fromEntries(packet.deals.map((d) => [d.deal_id, d.dealname]));
  const nm = (id) => esc(names[id] || id);
  const sec = (title, items, render) => items && items.length ?
    `<div style="margin-top:12px"><div class="section-title" style="margin:6px 0">${title}</div>${items.map(render).join("")}</div>` : "";
  return `
    <div style="font-size:13px">${esc(n.summary || "")}</div>
    ${sec("Discuss in call", n.deals_to_discuss, (d) => `<div class="flag-row"><span class="deal" data-ins="${d.deal_id}">${nm(d.deal_id)}</span> — ${esc(d.reason)}</div>`)}
    ${sec("Anomalies", n.anomalies, (d) => `<div class="flag-row"><span class="badge warning">${esc(d.flag_code || "issue")}</span> <span class="deal" data-ins="${d.deal_id}">${nm(d.deal_id)}</span> — ${esc(d.issue)}</div>`)}
    ${sec("Should be closed-lost", n.should_be_closed_lost, (d) => `<div class="flag-row"><span class="badge critical">close-lost</span> <span class="deal" data-ins="${d.deal_id}">${nm(d.deal_id)}</span> — ${esc(d.rationale)}</div>`)}`;
}

function dealRow(d) {
  const close = d.closedate ? `<span style="${d.closes_in_window ? "" : "color:var(--warning)"}">${fmt.date(d.closedate)}</span>` : "—";
  return `<tr class="row-click" data-deal="${d.deal_id}"><td>${esc(d.dealname)}</td><td>S${d.stage_order}</td>
    <td><span class="badge ${d.scenario}">${d.scenario}</span></td>
    <td class="num">${fmt.money(d.amount)}</td><td class="num">${d.p_win}</td><td class="num">${fmt.money(d.weighted_amount)}</td>
    <td class="num">${close}</td>
    <td class="num">${fmt.days(d.days_in_current_stage)}</td><td class="num">${d.days_since_engagement == null ? "—" : fmt.days(d.days_since_engagement)}</td>
    <td><div class="flag-list">${d.flags.map((f) => `<span class="badge ${f.severity}">${f.code.replace(/_/g, " ").toLowerCase()}</span>`).join("")}</div></td></tr>`;
}
function kpi(label, value, sub, cls) {
  return `<div class="card kpi"><div class="kpi-label">${label}</div><div class="kpi-value${cls ? " " + cls : ""}">${esc(value)}</div><div class="kpi-sub">${sub}</div></div>`;
}
function healthCls(h) { return { healthy: "good", watch: "warn", thin: "bad" }[h] || ""; }
