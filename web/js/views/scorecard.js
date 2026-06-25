import { api } from "../api.js";
import { fmt, esc, info } from "../util.js";
import { chart, PALETTE, gridOpts } from "../charts.js";

const TIP = {
  score: "0–100. A weighted blend of the four columns below (40% touched, 25% not-neglected, 20% late-stage cadence, 15% meeting discipline). Worst first. Click a rep for the breakdown.",
  value: "Total $ amount of this rep's open S1–S5 deals.",
  touched: "% of the rep's open deals with a logged sales activity (call / meeting / note / outbound email — not marketing email) in the last 7 days.",
  act: "Average sales activities logged per open deal per week, over the trailing 4 weeks.",
  neglect: "% of the rep's open deals carrying at least one hard hygiene flag (Stage-0 aging, S1→S3 slow, late-stage silent, owner issue, missing amount, stale close date).",
  held: "Meetings actually held ÷ meetings booked. A low rate means lots of no-shows, cancellations, or reschedules.",
  jumpers: "Deals advanced between stages in under an hour — usually pipeline-padding rather than real progression.",
};

let selected = null;

export async function render(el, ctx) {
  const d = await api.scorecard();
  el.innerHTML = `
    <div class="panel"><h3>Rep Scorecard <span class="panel-sub">Working Score = 40% touch coverage + 25% (1−neglect) + 20% late-stage cadence + 15% meeting discipline · worst first</span></h3>
      <div class="table-scroll"><table><thead><tr>
        <th>Rep</th><th class="num">Working Score${info(TIP.score)}</th><th class="num">Open</th><th class="num">Value${info(TIP.value)}</th>
        <th class="num">Touched 7d${info(TIP.touched)}</th><th class="num">Act/deal/wk${info(TIP.act)}</th><th class="num">Neglect${info(TIP.neglect)}</th>
        <th class="num">Held rate${info(TIP.held)}</th><th class="num">Jumpers${info(TIP.jumpers)}</th></tr></thead>
        <tbody>${d.reps.map(row).join("")}</tbody></table></div></div>
    <div id="sc-detail"></div>`;

  el.querySelectorAll("[data-rep]").forEach((tr) => tr.onclick = () => {
    selected = tr.dataset.rep === "null" ? null : tr.dataset.rep;
    detail(el, ctx, d.reps.find((r) => String(r.owner_id) === tr.dataset.rep));
  });
}

function row(r) {
  const sc = r.working_score;
  const col = sc < 40 ? "var(--critical)" : sc < 65 ? "var(--warning)" : "var(--good)";
  return `<tr class="row-click" data-rep="${r.owner_id}">
    <td>${esc(r.name)} ${r.in_roster ? "" : '<span class="pill">off-roster</span>'}</td>
    <td class="num"><span style="display:inline-flex;align-items:center;gap:8px;justify-content:flex-end">
      <span class="bar-cell" style="width:${Math.max(4, sc * 0.6)}px;background:${col}"></span><strong>${sc}</strong></span></td>
    <td class="num">${r.open_deals}</td><td class="num">${fmt.money(r.value_open)}</td>
    <td class="num">${fmt.pct(r.pct_touched_7d)}</td><td class="num">${r.avg_activities_per_open_deal_per_week}</td>
    <td class="num">${fmt.pct(r.neglect_rate)}</td><td class="num">${fmt.pct(r.held_rate)}</td>
    <td class="num">${r.stage_jumper_count || ""}</td></tr>`;
}

async function detail(el, ctx, r) {
  const wrap = el.querySelector("#sc-detail");
  if (!r) { wrap.innerHTML = ""; return; }
  const c = r.score_components;
  wrap.innerHTML = `
    <div class="cols-2">
      <div class="panel"><h3>${esc(r.name)} · Working Score ${r.working_score}</h3>
        <table><tbody>
          ${comp("Touch coverage (7d)", c.touch_coverage, 0.40)}
          ${comp("Not neglected", c.neglect_inverse, 0.25)}
          ${comp("Late-stage cadence", c.late_stage_cadence, 0.20)}
          ${comp("Meeting discipline", c.meeting_discipline, 0.15)}
        </tbody></table>
        <div class="muted small" style="margin-top:8px">Meetings held 28d: ${r.meetings_held_28d} · booked-not-held: ${r.booked_not_held} · jumpers: ${r.stage_jumper_count}</div></div>
      <div class="panel"><h3>Activity Cadence <span class="panel-sub">last 8 weeks</span></h3>
        <div class="chart-wrap"><canvas id="sc-cad"></canvas></div></div>
    </div>
    <div class="panel"><h3>Flagged Open Deals</h3><div id="sc-deals"><div class="loading">Loading…</div></div></div>`;

  chart("sc-cad", {
    type: "bar",
    data: { labels: r.cadence.map((w) => w.week),
      datasets: [
        { label: "Activities", backgroundColor: PALETTE[0], data: r.cadence.map((w) => w.activities) },
        { label: "Meetings held", backgroundColor: PALETTE[1], data: r.cadence.map((w) => w.meetings_held) },
      ] },
    options: gridOpts,
  });

  if (r.owner_id) {
    const dd = await api.deals({ owner_id: r.owner_id });
    const flagged = dd.deals.filter((x) => x.hard_count > 0);
    el.querySelector("#sc-deals").innerHTML = `<div class="table-scroll"><table><thead><tr><th>Deal</th><th>Stage</th><th class="num">Days in stage</th><th class="num">Last activity</th><th>Flags</th></tr></thead>
      <tbody>${flagged.map((x) => `<tr class="row-click" data-deal="${x.deal_id}"><td>${esc(x.dealname)}</td><td>${x.stage}</td><td class="num">${fmt.days(x.days_in_stage)}</td><td class="num">${x.days_since_activity == null ? "—" : fmt.days(x.days_since_activity)}</td><td><div class="flag-list">${x.flags.map((f) => `<span class="pill">${f.replace(/_/g, " ").toLowerCase()}</span>`).join("")}</div></td></tr>`).join("") || `<tr><td colspan=5 class=empty>No flagged deals.</td></tr>`}</tbody></table></div>`;
    el.querySelectorAll("#sc-deals [data-deal]").forEach((tr) => tr.onclick = () => ctx.openInspector(tr.dataset.deal));
  } else {
    el.querySelector("#sc-deals").innerHTML = `<div class="muted small">Unassigned bucket.</div>`;
  }
  wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function comp(label, val, weight) {
  return `<tr><td>${label} <span class="muted small">(${Math.round(weight * 100)}%)</span></td>
    <td class="num" style="width:50%"><span class="bar-cell" style="width:${(val || 0) * 140}px"></span> ${fmt.pct(val)}</td></tr>`;
}
