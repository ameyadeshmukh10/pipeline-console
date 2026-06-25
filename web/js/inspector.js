import { fmt, esc } from "./util.js";

const ICON = { meeting: "📅", call: "📞", email: "✉️", note: "📝", task: "✓", stage: "◧" };

export function inspectorHTML(d) {
  const a = d.activity;
  const strip = d.stage_timeline.map((s, i) =>
    `${i ? '<span class="tl-arrow">→</span>' : ""}<span class="tl-stage ${s.is_current ? "current" : ""}" title="${esc(s.label)} · ${fmt.days(s.duration_days)}">${s.short} <span class="muted small">${s.duration_days != null ? fmt.days(s.duration_days) : ""}</span></span>`).join("");

  return `
    <div class="insp-head">
      <div>
        <div class="insp-title">${esc(d.dealname || d.deal_id)}</div>
        <div class="muted small">${d.stage} · ${esc(d.owner)} ${d.owner_mismatch ? '<span class="badge critical">owner mismatch</span>' : ""} · ${fmt.money(d.amount)} · close ${fmt.date(d.closedate)}</div>
      </div>
      <div class="flag-list">${d.flags.map((f) => `<span class="badge ${f.severity}" title="${esc(f.fact)}">${esc(f.label)}</span>`).join("")}</div>
    </div>

    <div class="timeline-strip">${strip || '<span class="muted small">No stage history</span>'}</div>
    ${d.skipped_stages.length ? `<div class="muted small">Skipped: ${d.skipped_stages.join(", ")}</div>` : ""}

    <div class="metric-grid">
      ${metric("Time in stage", fmt.days(d.current_time_in_stage_days))}
      ${metric("Days since activity", a.days_since_last_activity == null ? "—" : fmt.days(a.days_since_last_activity))}
      ${metric("Meetings held / booked", `${a.meetings_held}/${a.meetings_booked}`)}
      ${metric("Since stage entry", a.since_stage_entry + " acts")}
      ${metric("Logged by owner", a.pct_logged_by_owner == null ? "—" : fmt.pct(a.pct_logged_by_owner))}
      ${metric("Next step", a.has_next_step ? "✓" : "none")}
      ${metric("Inbound / outbound", `${a.inbound}/${a.outbound}`)}
      ${metric("Total cycle", fmt.days(d.total_cycle_days))}
    </div>

    <div class="muted small">Activity counts (human): 📅 ${a.counts.meetings} · 📞 ${a.counts.calls} · ✉️ ${a.counts.emails} · 📝 ${a.counts.notes} · ✓ ${a.counts.tasks}</div>

    <div class="section-title" style="margin:18px 0 6px">Timeline ${d.feed_truncated ? `<span class="muted small">(showing recent ${d.feed.length} of ${d.total_engagements})</span>` : ""}</div>
    <div class="feed">
      ${d.feed.map(feedItem).join("") || '<div class="empty">No activity logged.</div>'}
    </div>`;
}

function feedItem(f) {
  if (f.kind === "stage")
    return `<div class="feed-item stage"><div class="feed-icon">◧</div>
      <div><div><strong>Entered ${f.short}</strong> — ${esc(f.label)}</div><div class="feed-meta">${fmt.datetime(f.ts)}</div></div></div>`;
  const dir = f.direction === "in" ? "↙ in" : f.direction === "out" ? "↗ out" : "";
  return `<div class="feed-item ${f.is_automated ? "automated" : ""}"><div class="feed-icon">${ICON[f.type] || "•"}</div>
    <div><div>${esc(f.subject || f.type)} ${dir ? `<span class="muted small">${dir}</span>` : ""} ${f.status ? `<span class="pill">${esc(f.status)}</span>` : ""}</div>
      <div class="feed-meta">${fmt.datetime(f.ts)} · ${esc(f.owner)}${f.is_automated ? " · automated" : ""}</div></div></div>`;
}
function metric(label, value) {
  return `<div class="metric"><div class="m-label">${label}</div><div class="m-value">${esc(value)}</div></div>`;
}
