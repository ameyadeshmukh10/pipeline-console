import { api } from "./api.js";
import { fmt, esc } from "./util.js";
import { destroyAll } from "./charts.js";
import { inspectorHTML } from "./inspector.js";
import * as prepipeline from "./views/prepipeline.js";
import * as execution from "./views/execution.js";
import * as forecast from "./views/forecast.js";
import * as cohorts from "./views/cohorts.js";
import * as deals from "./views/deals.js";
import * as scorecard from "./views/scorecard.js";
import * as settings from "./views/settings.js";

const ROUTES = {
  prepipeline: { title: "Pre-Pipeline", sub: "SDR quality — weekly Stage-0 creation & conversion", mod: prepipeline },
  execution: { title: "Pipeline Execution", sub: "AE motion — assignments, progression, velocity", mod: execution },
  forecast: { title: "Forecast", sub: "Per-AE weighted forecast + agent analysis", mod: forecast },
  cohorts: { title: "Cohort Analytics", sub: "Deals cohorted by create week", mod: cohorts },
  deals: { title: "Deals", sub: "Deal inspector & hygiene", mod: deals },
  scorecard: { title: "Rep Scorecard", sub: "Are reps actually working their deals?", mod: scorecard },
  settings: { title: "Settings", sub: "Targets, probabilities, roster", mod: settings },
};

const view = document.getElementById("view");
const ctx = { meta: null, openInspector, refreshFlags, refreshMeta };

function currentRoute() { return location.hash.replace("#/", "") || "prepipeline"; }

async function navigate(route) {
  if (!ROUTES[route]) route = "prepipeline";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === route));
  const r = ROUTES[route];
  document.getElementById("page-title").textContent = r.title;
  document.getElementById("page-sub").textContent = r.sub;
  destroyAll();
  view.innerHTML = `<div class="loading">Loading…</div>`;
  try { await r.mod.render(view, ctx); }
  catch (e) { view.innerHTML = `<div class="empty">Error: ${esc(e.message)}</div>`; console.error(e); }
}

document.getElementById("nav").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-route]");
  if (!a) return;
  e.preventDefault();
  location.hash = "#/" + a.dataset.route;
});
window.addEventListener("hashchange", () => navigate(currentRoute()));

// --- Sync controller ---
const syncBtn = document.getElementById("sync-btn");
const syncBar = document.getElementById("sync-bar");
const syncFill = document.getElementById("sync-fill");
const syncLabel = document.getElementById("sync-label");
let polling = null;

syncBtn.onclick = async () => {
  syncBtn.disabled = true;
  try { await api.sync(); } catch (e) { /* 409 = already running; just poll */ }
  syncBar.classList.remove("hidden", "error");
  pollSync();
};

function pollSync() {
  clearInterval(polling);
  polling = setInterval(async () => {
    let s;
    try { s = await api.syncStatus(); } catch (e) { return; }
    syncFill.style.width = s.pct + "%";
    syncLabel.textContent = s.phase_label +
      (s.phase === "fetching_deals" && s.deals_total ? ` (${s.deals_seen}/${s.deals_total})` : "");
    if (!s.running) {
      clearInterval(polling);
      syncBtn.disabled = false;
      if (s.error) { syncBar.classList.add("error"); syncLabel.textContent = "Error: " + s.error; }
      else { syncLabel.textContent = `Done in ${s.request_count} requests`; setTimeout(() => syncBar.classList.add("hidden"), 2000); }
      await refreshMeta();
      navigate(currentRoute());
    }
  }, 800);
}

async function refreshMeta() {
  ctx.meta = await api.meta();
  document.getElementById("last-sync").textContent = fmt.ago(ctx.meta.last_success_at);
  const w = ctx.meta.window;
  document.getElementById("window-label").textContent = w && w.start
    ? `${fmt.dateISO(w.start)} → ${w.continuous ? "ongoing" : fmt.dateISO(w.end)}`
    : "—";
  refreshFlags();
}

async function refreshFlags() {
  try {
    const f = await api.flags();
    document.getElementById("flags-count").textContent = f.total_flagged;
    window.__flags = f;
  } catch (e) { /* ignore */ }
}

// --- Flags drawer ---
const drawer = document.getElementById("drawer");
document.getElementById("flags-btn").onclick = async () => {
  drawer.classList.remove("hidden");
  const body = document.getElementById("drawer-body");
  body.innerHTML = `<div class="loading">Loading…</div>`;
  const f = window.__flags || (await api.flags());
  const sev = f.by_severity || {};
  body.innerHTML =
    `<div class="muted small" style="margin-bottom:10px">${f.total_flagged} deals flagged · ${sev.critical || 0} critical · ${sev.warning || 0} warning · ${sev.info || 0} info</div>` +
    f.deals.slice(0, 150).map((d) =>
      `<div class="flag-row"><div class="fr-head"><span class="deal" data-deal="${d.deal_id}">${esc(d.dealname)}</span><span class="muted small">${d.stage || ""}</span></div>
        <div class="flag-list" style="margin-top:5px">${d.flags.map((fl) => `<span class="badge ${fl.severity}" title="${esc(fl.fact)}">${esc(fl.label)}</span>`).join("")}</div></div>`).join("");
  body.querySelectorAll("[data-deal]").forEach((x) => x.onclick = () => { drawer.classList.add("hidden"); openInspector(x.dataset.deal); });
};
document.getElementById("drawer-close").onclick = () => drawer.classList.add("hidden");

// --- Inspector modal ---
const modal = document.getElementById("modal");
const overlay = document.getElementById("overlay");
async function openInspector(id) {
  modal.classList.remove("hidden");
  overlay.classList.remove("hidden");
  document.getElementById("modal-body").innerHTML = `<div class="loading">Loading…</div>`;
  try {
    const d = await api.deal(id);
    document.getElementById("modal-body").innerHTML = inspectorHTML(d);
  } catch (e) {
    document.getElementById("modal-body").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
function closeModal() { modal.classList.add("hidden"); overlay.classList.add("hidden"); }
document.getElementById("modal-close").onclick = closeModal;
overlay.onclick = closeModal;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeModal(); drawer.classList.add("hidden"); } });

// --- Boot ---
(async function boot() {
  try { await refreshMeta(); } catch (e) { console.error(e); }
  navigate(currentRoute());
})();
