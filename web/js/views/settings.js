import { api } from "../api.js";
import { fmt, esc, info } from "../util.js";

const TIP = {
  probsrc: "Where each deal's win-probability comes from. House = your editable per-stage map below; HubSpot = the deal's own hs_deal_stage_probability; Blend = the average of the two.",
  probmap: "Win-probability assigned to a deal based on its current stage. Used to compute each deal's weighted amount (amount × probability) and the Most-likely forecast.",
  aeTargets: "Each AE's quarter quota in $. Drives coverage (open pipeline ÷ target) and gap-to-target on the Forecast tab.",
  cadence: "Targets the flags & velocity reports check against. S0→S1: hand to an AE within N days. S1→S3: reach Proposal within N days. S4/S5: a late-stage deal must show activity within N days.",
};

export async function render(el, ctx) {
  const meta = ctx.meta || (await api.meta());
  const d = await api.settings();
  const v = d.values;
  const probs = v.stage_probabilities || {};
  const aeT = v.ae_targets || {};
  const openStages = meta.stages.filter((s) => s.is_open || s.is_won);
  const targets = (await api.forecastTargets()).targets;

  el.innerHTML = `
    <div class="cols-2">
      <div class="panel"><h3>Cadence Targets ${info(TIP.cadence)}<span class="panel-sub"> days</span></h3>
        <div class="metric-grid">
          ${numField("s0_to_s1_target_days", "S0 → S1 target", v.s0_to_s1_target_days)}
          ${numField("s1_to_s3_target_days", "S1 → S3 target", v.s1_to_s3_target_days)}
          ${numField("late_stage_activity_days", "S4/S5 activity window", v.late_stage_activity_days)}
          ${numField("cold_open_days", "Cold-open threshold", v.cold_open_days)}
        </div></div>
      <div class="panel"><h3>Forecast Probability ${info(TIP.probmap)}</h3>
        <label class="field" style="max-width:240px">Probability source ${info(TIP.probsrc)}
          <select id="set-probsrc">${["house", "hubspot", "blend"].map((o) => `<option ${v.probability_source === o ? "selected" : ""}>${o}</option>`).join("")}</select></label>
        <div class="metric-grid" style="margin-top:14px">
          ${openStages.map((s) => `<label class="field">${s.short} ${esc(s.label)}
            <input data-prob="${s.stage_id}" type="number" step="0.05" min="0" max="1" value="${probs[s.stage_id] ?? ""}"/></label>`).join("")}
        </div></div>
    </div>

    <div class="panel"><h3>Per-AE Quarter Targets ${info(TIP.aeTargets)}<span class="panel-sub"> $ — drives coverage & gap</span></h3>
      <div class="metric-grid">
        ${numField("org_target", "Org default target", v.org_target)}
        ${targets.map((t) => `<label class="field">${esc(t.name)}
          <input data-aet="${t.owner_id}" type="number" step="1000" value="${aeT[t.owner_id] ?? ""}" placeholder="$"/></label>`).join("")}
      </div></div>

    <div class="panel"><h3>Owner Roster <span class="panel-sub">seeded from config · role attribution drives flags & reports</span></h3>
      <table><thead><tr><th>Owner</th><th>SDR</th><th>AE</th><th>SE</th><th>Never owns</th><th>Full lifecycle</th><th>Notes</th></tr></thead>
        <tbody>${d.roster.map((r) => `<tr><td>${esc(r.display_name)}</td>${["is_sdr", "is_ae", "is_se", "never_owns", "full_lifecycle"].map((k) => `<td>${r[k] ? "✓" : ""}</td>`).join("")}<td class="muted small">${esc(r.notes || "")}</td></tr>`).join("")}</tbody></table>
      ${d.off_roster_owners.length ? `<div class="muted small" style="margin-top:10px">Off-roster owners on deals (bucketed Other/Unknown): ${d.off_roster_owners.map((o) => `${o.owner_id} (${o.n})`).join(", ")}</div>` : ""}
    </div>

    <div style="margin-top:16px;display:flex;gap:12px;align-items:center">
      <button id="set-save" class="btn-primary">Save & recompute</button>
      <span id="set-msg" class="muted small"></span>
    </div>`;

  el.querySelector("#set-save").onclick = async () => {
    const values = {};
    el.querySelectorAll("[data-set]").forEach((i) => { if (i.value !== "") values[i.dataset.set] = +i.value; });
    const sp = { ...probs };
    el.querySelectorAll("[data-prob]").forEach((i) => { if (i.value !== "") sp[i.dataset.prob] = +i.value; });
    values.stage_probabilities = sp;
    values.probability_source = el.querySelector("#set-probsrc").value;
    const at = {};
    el.querySelectorAll("[data-aet]").forEach((i) => { if (i.value !== "") at[i.dataset.aet] = +i.value; });
    values.ae_targets = at;
    const msg = el.querySelector("#set-msg");
    msg.textContent = "Saving…";
    try { await api.putSettings(values); msg.textContent = "Saved. Metrics use the new settings on next view load."; }
    catch (e) { msg.textContent = "Error: " + e.message; }
  };
}

function numField(key, label, val) {
  return `<label class="field">${label}<input data-set="${key}" type="number" value="${val ?? ""}"/></label>`;
}
