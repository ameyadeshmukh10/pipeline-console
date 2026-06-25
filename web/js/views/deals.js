import { api } from "../api.js";
import { fmt, esc } from "../util.js";

const state = { owner: "", stage: "", onlyFlagged: false };

export async function render(el, ctx) {
  const meta = ctx.meta || (await api.meta());
  const owners = meta.roster.map((r) => [r.owner_id, r.display_name]);
  const stages = meta.stages.filter((s) => s.is_open);

  el.innerHTML = `
    <div class="panel"><div class="toolbar">
      <label class="field">Owner<select id="dl-owner"><option value="">All</option>
        ${owners.map(([id, n]) => `<option value="${id}" ${state.owner === id ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
      <label class="field">Stage<select id="dl-stage"><option value="">All open</option>
        ${stages.map((s) => `<option value="${s.stage_id}" ${state.stage === s.stage_id ? "selected" : ""}>${s.short} · ${esc(s.label)}</option>`).join("")}</select></label>
      <label class="field" style="flex-direction:row;align-items:center;gap:6px;margin-top:18px">
        <input id="dl-flagged" type="checkbox" ${state.onlyFlagged ? "checked" : ""}/> Only flagged</label>
      <div style="margin-left:auto;align-self:center" class="muted small" id="dl-count"></div>
    </div></div>
    <div class="panel" id="dl-table"><div class="loading">Loading…</div></div>`;

  const reload = () => load(el, ctx);
  el.querySelector("#dl-owner").onchange = (e) => { state.owner = e.target.value; reload(); };
  el.querySelector("#dl-stage").onchange = (e) => { state.stage = e.target.value; reload(); };
  el.querySelector("#dl-flagged").onchange = (e) => { state.onlyFlagged = e.target.checked; reload(); };
  await load(el, ctx);
}

async function load(el, ctx) {
  const params = {};
  if (state.owner) params.owner_id = state.owner;
  if (state.stage) params.stage = state.stage;
  const d = await api.deals(params);
  let rows = d.deals;
  if (state.onlyFlagged) rows = rows.filter((r) => r.hard_count > 0);
  el.querySelector("#dl-count").textContent = `${rows.length} deals`;
  el.querySelector("#dl-table").innerHTML = `
    <div class="table-scroll"><table><thead><tr>
      <th>Deal</th><th>Owner</th><th>Stage</th><th class="num">Days in stage</th>
      <th class="num">Last activity</th><th class="num">Meetings</th><th class="num">Next step</th><th>Flags</th></tr></thead>
      <tbody>${rows.map(row).join("") || `<tr><td colspan=8 class=empty>No deals match.</td></tr>`}</tbody></table></div>`;
  el.querySelectorAll("[data-deal]").forEach((tr) => tr.onclick = () => ctx.openInspector(tr.dataset.deal));
}

function row(r) {
  const stale = r.days_since_activity != null && r.days_since_activity > 14;
  const old = r.days_in_stage != null && r.days_in_stage > 20;
  return `<tr class="row-click" data-deal="${r.deal_id}">
    <td>${esc(r.dealname)}</td><td>${esc(r.owner)}</td><td>${r.stage}</td>
    <td class="num" style="${old ? "color:var(--critical);font-weight:600" : ""}">${fmt.days(r.days_in_stage)}</td>
    <td class="num" style="${stale ? "color:var(--critical);font-weight:600" : ""}">${r.days_since_activity == null ? "—" : fmt.days(r.days_since_activity)}</td>
    <td class="num">${r.n_meetings_held}/${r.n_meetings_booked}</td>
    <td class="num">${r.has_next_step ? "✓" : "<span class='badge warning'>none</span>"}</td>
    <td><div class="flag-list">${r.flags.slice(0, 4).map((f) => `<span class="pill">${f.replace(/_/g, " ").toLowerCase()}</span>`).join("")}${r.flags.length > 4 ? `<span class="pill">+${r.flags.length - 4}</span>` : ""}</div></td></tr>`;
}
