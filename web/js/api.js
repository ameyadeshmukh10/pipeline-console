// Thin fetch wrappers for the JSON API.
async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} -> ${r.status} ${await r.text()}`);
  return r.json();
}
async function send(path, method, body) {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} -> ${r.status} ${await r.text()}`);
  return r.json();
}

export const api = {
  meta: () => getJSON("/api/meta"),
  settings: () => getJSON("/api/settings"),
  putSettings: (values) => send("/api/settings", "PUT", { values }),
  syncStatus: () => getJSON("/api/sync/status"),
  sync: () => send("/api/sync", "POST"),
  prepipeline: () => getJSON("/api/prepipeline"),
  execution: () => getJSON("/api/execution"),
  flags: () => getJSON("/api/flags"),
  cohorts: () => getJSON("/api/cohorts"),
  scorecard: () => getJSON("/api/scorecard"),
  forecastTargets: () => getJSON("/api/forecast/targets"),
  forecast: (ownerId, target) =>
    getJSON(`/api/forecast?owner_id=${encodeURIComponent(ownerId)}` + (target ? `&target=${target}` : "")),
  forecastRun: (owner_id, target) => send("/api/forecast/run", "POST", { owner_id, target }),
  deals: (params) => getJSON("/api/deals" + (params ? "?" + new URLSearchParams(params) : "")),
  deal: (id) => getJSON("/api/deals/" + encodeURIComponent(id)),
};
