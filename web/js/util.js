// Formatting + tiny DOM helpers.
export const fmt = {
  money(v) {
    if (v == null) return "—";
    const a = Math.abs(v);
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (a >= 1e3) return "$" + (v / 1e3).toFixed(1) + "k";
    return "$" + Math.round(v);
  },
  pct(v, d = 0) { return v == null ? "—" : (v * 100).toFixed(d) + "%"; },
  num(v) { return v == null ? "—" : v; },
  days(v) { return v == null ? "—" : (+v).toFixed(1) + "d"; },
  date(s) { return s ? new Date(s).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—"; },
  datetime(s) { return s ? new Date(s).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—"; },
  ago(s) {
    if (!s) return "never";
    const d = (Date.now() - new Date(s).getTime()) / 1000;
    if (d < 60) return "just now";
    if (d < 3600) return Math.round(d / 60) + "m ago";
    if (d < 86400) return Math.round(d / 3600) + "h ago";
    return Math.round(d / 86400) + "d ago";
  },
};

export function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
export function esc(s) {
  return (s == null ? "" : String(s)).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
export function info(text) {
  return `<span class="info" title="${esc(text)}" tabindex="0">ⓘ</span>`;
}
export function trendTag(t) {
  if (!t || t.direction === "insufficient_data")
    return `<span class="tag-insufficient_data">insufficient data${t ? " (n=" + t.n + ")" : ""}</span>`;
  const sym = { faster: "▼ faster", slower: "▲ slower", flat: "▬ flat",
                rising: "▲ rising", falling: "▼ falling" }[t.direction] || t.direction;
  return `<span class="tag-${t.direction}">${sym}</span>`;
}
