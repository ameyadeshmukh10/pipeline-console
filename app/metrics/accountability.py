"""Report 4 — Deal-level forensic visibility + rep accountability.

Per-deal inspector (stage timeline + merged activity feed + metrics) and a rep
scorecard with a transparent Working Score. Reads the SQLite mirror + rollups.
"""
from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional

import aiosqlite

from .. import db
from ..constants import STAGE_BY_ID, STAGE_ORDER_BY_ID
from .common import load_deal_views, load_role_sets, owner_display
from .flags import compute_flags
from .windows import (days_between, iso_week_key, iso_weeks_in_range, now_utc,
                      parse_ts, week_label)

HARD = {"critical", "warning"}
STAGE_SHORTS = {0: "S0", 1: "S1", 2: "S2", 3: "S3", 4: "S4", 5: "S5", 6: "WON", 7: "LOST"}


async def _rollups(conn) -> Dict[str, Dict[str, Any]]:
    return {r["deal_id"]: dict(r) for r in
            await (await conn.execute("SELECT * FROM deal_activity_rollup")).fetchall()}


# ---------------------------------------------------------------------------
# Deals table (the Deals view list)
# ---------------------------------------------------------------------------
async def deals_table(conn: aiosqlite.Connection, owner_id: Optional[str] = None,
                      stage: Optional[str] = None) -> Dict[str, Any]:
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    views = await load_deal_views(conn)
    rollups = await _rollups(conn)
    now = now_utc()

    rows: List[Dict[str, Any]] = []
    for v in views.values():
        if not v.is_open:
            continue
        if owner_id and v.owner_id != owner_id:
            continue
        if stage and v.dealstage != stage:
            continue
        roll = rollups.get(v.deal_id, {})
        entered = v.entries.get(v.current_order)
        flags = compute_flags(v, roll, roles, s, now)
        hard = [f for f in flags if f["severity"] in HARD]
        rows.append({
            "deal_id": v.deal_id, "dealname": v.dealname,
            "owner": owner_display(v.owner_id, roles), "owner_id": v.owner_id,
            "stage": STAGE_SHORTS.get(v.current_order, v.dealstage), "stage_order": v.current_order,
            "amount": v.amount,
            "days_in_stage": round(days_between(entered, now), 1) if entered else None,
            "days_since_activity": round(roll["days_since_last_activity"], 1)
                if roll.get("days_since_last_activity") is not None else None,
            "n_meetings_held": roll.get("n_meetings_held", 0),
            "n_meetings_booked": roll.get("n_meetings_booked", 0),
            "n_total": roll.get("n_total", 0),
            "has_next_step": roll.get("has_next_step", 0),
            "flags": [f["code"] for f in flags],
            "hard_count": len(hard),
        })
    rows.sort(key=lambda r: (-r["hard_count"], -(r["days_since_activity"] or 0)))
    return {"deals": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# Per-deal inspector
# ---------------------------------------------------------------------------
async def deal_inspector(conn: aiosqlite.Connection, deal_id: str) -> Optional[Dict[str, Any]]:
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    # roster_only=False — the inspector must open ANY deal (incl. off-roster /
    # archived owners surfaced by aging, flags, etc.), not just roster-owned ones.
    views = await load_deal_views(conn, roster_only=False)
    now = now_utc()
    v = views.get(deal_id)
    if v is None:
        return None
    roll = (await _rollups(conn)).get(deal_id, {})

    # stage timeline
    ev_rows = await (await conn.execute(
        """SELECT stage_id, stage_order, entered_at, exited_at, dwell_seconds, is_current
           FROM deal_stage_events WHERE deal_id=? ORDER BY entered_at""", (deal_id,))).fetchall()
    stage_timeline = []
    for r in ev_rows:
        meta = STAGE_BY_ID.get(r["stage_id"], {})
        stage_timeline.append({
            "stage_id": r["stage_id"], "short": meta.get("short"), "label": meta.get("label"),
            "stage_order": r["stage_order"], "entered_at": r["entered_at"], "exited_at": r["exited_at"],
            "duration_days": round(r["dwell_seconds"] / 86400.0, 1) if r["dwell_seconds"] is not None else None,
            "is_current": r["is_current"], "open": r["exited_at"] is None,
        })
    visited = {r["stage_order"] for r in ev_rows}
    max_order = max(visited) if visited else 0
    skipped = [STAGE_SHORTS[o] for o in range(0, min(max_order, 6) + 1) if o not in visited]

    # activities + merged feed (cap to most-recent for payload size)
    FEED_LIMIT = 200
    total_eng = (await (await conn.execute(
        "SELECT COUNT(*) n FROM engagements WHERE deal_id=?", (deal_id,))).fetchone())["n"]
    eng = await (await conn.execute(
        """SELECT raw_id, type, ts, owner_id, direction, status, subject, is_automated
           FROM engagements WHERE deal_id=? ORDER BY ts DESC LIMIT ?""",
        (deal_id, FEED_LIMIT))).fetchall()
    activities = [{
        "id": e["raw_id"], "type": e["type"], "ts": e["ts"],
        "owner": owner_display(e["owner_id"], roles), "owner_id": e["owner_id"],
        "direction": e["direction"], "status": e["status"], "subject": e["subject"],
        "is_automated": e["is_automated"],
    } for e in eng]

    feed = [{"kind": "activity", **a} for a in activities]
    for st in stage_timeline:
        feed.append({"kind": "stage", "ts": st["entered_at"], "short": st["short"],
                     "label": st["label"], "stage_order": st["stage_order"]})
    feed.sort(key=lambda x: x["ts"] or "", reverse=True)
    feed_truncated = total_eng > FEED_LIMIT

    flags = compute_flags(v, roll, roles, s, now)
    entered = v.entries.get(v.current_order)
    cad = _meeting_cadence(roll)

    return {
        "deal_id": deal_id, "dealname": v.dealname,
        "owner": owner_display(v.owner_id, roles), "owner_id": v.owner_id,
        "owner_mismatch": v.is_open and (v.owner_id is None or v.owner_id not in roles.roster
                                          or v.owner_id in roles.never_owns
                                          or (v.owner_id in roles.se and (v.current_order or 0) >= 1)),
        "stage": STAGE_SHORTS.get(v.current_order, v.dealstage), "stage_order": v.current_order,
        "amount": v.amount, "is_open": v.is_open,
        "closedate": v.closedate.isoformat() if v.closedate else None,
        "createdate": v.createdate.isoformat() if v.createdate else None,
        "current_time_in_stage_days": round(days_between(entered, now), 1) if entered else None,
        "total_cycle_days": round(days_between(v.createdate, v.closedate or now), 1) if v.createdate else None,
        "skipped_stages": skipped,
        "stage_timeline": stage_timeline,
        "activity": {
            "last_activity_ts": roll.get("last_activity_ts"),
            "days_since_last_activity": roll.get("days_since_last_activity"),
            "counts": {"meetings": roll.get("n_meetings", 0), "calls": roll.get("n_calls", 0),
                       "emails": roll.get("n_emails", 0), "notes": roll.get("n_notes", 0),
                       "tasks": roll.get("n_tasks", 0), "total": roll.get("n_total", 0)},
            "since_stage_entry": roll.get("n_since_stage_total", 0),
            "outbound": roll.get("n_outbound", 0), "inbound": roll.get("n_inbound", 0),
            "pct_logged_by_owner": roll.get("pct_activity_logged_by_owner"),
            "has_next_step": roll.get("has_next_step", 0),
            "has_open_task": roll.get("has_open_task", 0),
            **cad,
        },
        "flags": flags,
        "feed": feed,
        "feed_truncated": feed_truncated,
        "total_engagements": total_eng,
    }


def _meeting_cadence(roll: Dict[str, Any]) -> Dict[str, Any]:
    booked = roll.get("n_meetings_booked", 0) or 0
    held = roll.get("n_meetings_held", 0) or 0
    first = parse_ts(roll.get("first_meeting_ts"))
    last = parse_ts(roll.get("last_meeting_ts"))
    avg_gap = None
    if first and last and held > 1:
        avg_gap = round(days_between(first, last) / max(held - 1, 1), 1)
    return {
        "meetings_booked": booked, "meetings_held": held,
        "booked_not_held": max(booked - held, 0),
        "days_since_last_meeting": round(days_between(last, now_utc()), 1) if last else None,
        "avg_days_between_meetings": avg_gap,
    }


# ---------------------------------------------------------------------------
# Rep scorecard
# ---------------------------------------------------------------------------
async def rep_scorecard(conn: aiosqlite.Connection) -> Dict[str, Any]:
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    views = await load_deal_views(conn)
    rollups = await _rollups(conn)
    now = now_utc()
    late_days = int(s.get("late_stage_activity_days", 7))

    last4 = set(iso_weeks_in_range(now - timedelta(days=28), now))
    last8 = iso_weeks_in_range(now - timedelta(days=56), now)

    # weekly activity per owner
    rw = await (await conn.execute("SELECT * FROM rep_week_rollup")).fetchall()
    acts4: Dict[str, int] = defaultdict(int)
    held4: Dict[str, int] = defaultdict(int)
    cadence: Dict[str, Dict] = defaultdict(dict)
    for r in rw:
        owner = r["owner_id"]
        wk = (r["iso_year"], r["iso_week"])
        if wk in last4:
            acts4[owner] += r["activities_logged"]
            held4[owner] += r["meetings_held"]
        cadence[owner][wk] = {"activities": r["activities_logged"], "meetings_held": r["meetings_held"]}

    # stage jumpers (near-instant transitions) per owner
    jump_rows = await (await conn.execute(
        """SELECT d.owner_id, COUNT(DISTINCT t.deal_id) n FROM deal_stage_transitions t
           JOIN deals d ON d.deal_id=t.deal_id
           WHERE t.gap_seconds < 3600 AND t.to_stage_order BETWEEN 1 AND 5
           GROUP BY d.owner_id""")).fetchall()
    jumpers = {r["owner_id"]: r["n"] for r in jump_rows}

    agg: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "open_deals": 0, "value_open": 0.0, "touched7": 0, "hard_deals": 0,
        "held": 0, "booked": 0, "late_open": 0, "late_touched": 0})
    for v in views.values():
        if not v.is_open:
            continue
        owner = v.owner_id or "unassigned"
        roll = rollups.get(v.deal_id, {})
        a = agg[owner]
        a["open_deals"] += 1
        a["value_open"] += (v.amount or 0.0)
        dsa = roll.get("days_since_last_activity")
        if dsa is not None and dsa <= 7:
            a["touched7"] += 1
        flags = compute_flags(v, roll, roles, s, now)
        if any(f["severity"] in HARD for f in flags):
            a["hard_deals"] += 1
        a["held"] += roll.get("n_meetings_held", 0) or 0
        a["booked"] += roll.get("n_meetings_booked", 0) or 0
        if v.current_order in (4, 5):
            a["late_open"] += 1
            if dsa is not None and dsa <= late_days:
                a["late_touched"] += 1

    rows: List[Dict[str, Any]] = []
    for owner, a in agg.items():
        n = a["open_deals"] or 1
        touch = a["touched7"] / n
        neglect = a["hard_deals"] / n
        late_compliance = (a["late_touched"] / a["late_open"]) if a["late_open"] else 1.0
        discipline = (a["held"] / a["booked"]) if a["booked"] else 1.0
        score = 100 * (0.40 * touch + 0.25 * (1 - neglect) + 0.20 * late_compliance + 0.15 * discipline)
        cad_series = [{"week": week_label(*wk),
                       "activities": cadence.get(owner, {}).get(wk, {}).get("activities", 0),
                       "meetings_held": cadence.get(owner, {}).get(wk, {}).get("meetings_held", 0)}
                      for wk in last8]
        rows.append({
            "owner_id": None if owner == "unassigned" else owner,
            "name": owner_display(None if owner == "unassigned" else owner, roles),
            "in_roster": owner in roles.roster,
            "open_deals": a["open_deals"], "value_open": round(a["value_open"], 2),
            "pct_touched_7d": round(touch, 3),
            "avg_activities_per_open_deal_per_week": round(acts4.get(owner, 0) / n / 4.0, 2),
            "neglect_rate": round(neglect, 3),
            "hard_flag_deals": a["hard_deals"],
            "meetings_held_28d": held4.get(owner, 0),
            "held_rate": round(discipline, 3),
            "booked_not_held": max(a["booked"] - a["held"], 0),
            "stage_jumper_count": jumpers.get(owner, 0),
            "working_score": round(score, 1),
            "score_components": {
                "touch_coverage": round(touch, 3), "neglect_inverse": round(1 - neglect, 3),
                "late_stage_cadence": round(late_compliance, 3), "meeting_discipline": round(discipline, 3),
            },
            "cadence": cad_series,
        })
    rows.sort(key=lambda r: r["working_score"])
    return {"reps": rows, "weights": {"touch_coverage": 0.40, "neglect_inverse": 0.25,
                                      "late_stage_cadence": 0.20, "meeting_discipline": 0.15}}
