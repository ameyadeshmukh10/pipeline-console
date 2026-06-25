"""Materialize per-deal activity rollups and per-rep weekly activity at sync
time, so the inspector / scorecard / activity flags are O(1) reads.

Activity metrics are HUMAN-ONLY by default (is_automated=0) so marketing/bulk
emails never inflate a rep's "working it" signal. The inspector can still read
the raw ``engagements`` table for the full timeline with an automated toggle.
"""
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

import aiosqlite

from ..metrics.windows import iso_week_key, now_utc, parse_ts

_HELD_EXCLUDE = {"CANCELED", "NO_SHOW", "RESCHEDULED", "SCHEDULED"}


def _is_sales_activity(e) -> bool:
    """A rep proactively working the deal — excludes inbound/marketing email
    floods so 'last activity' / 'touched' reflect real sales effort."""
    if e["type"] in ("meeting", "call", "note", "task"):
        return True
    return e["type"] == "email" and e["direction"] == "out"


def meeting_held(status: Optional[str], ts: Optional[datetime], now: datetime) -> bool:
    s = (status or "").upper()
    if s == "COMPLETED":
        return True
    if ts is not None and ts <= now and s not in _HELD_EXCLUDE:
        return True  # in the past and not explicitly canceled -> inferred held
    return False


async def recompute_rollups(conn: aiosqlite.Connection, now: Optional[datetime] = None) -> int:
    now = now or now_utc()

    # current-stage entry per deal (for "since entering current stage" counts)
    cur_entry: Dict[str, Optional[datetime]] = {}
    for r in await (await conn.execute(
            "SELECT deal_id, entered_at FROM deal_stage_events WHERE is_current=1")).fetchall():
        cur_entry[r["deal_id"]] = parse_ts(r["entered_at"])

    deal_owner: Dict[str, Optional[str]] = {}
    open_ids = set()
    for r in await (await conn.execute("SELECT deal_id, owner_id, is_open FROM deals")).fetchall():
        deal_owner[r["deal_id"]] = r["owner_id"]
        if r["is_open"]:
            open_ids.add(r["deal_id"])

    by_deal: Dict[str, List[aiosqlite.Row]] = defaultdict(list)
    all_engs = await (await conn.execute("SELECT * FROM engagements")).fetchall()
    for e in all_engs:
        by_deal[e["deal_id"]].append(e)

    await conn.execute("DELETE FROM deal_activity_rollup")

    type_keys = {"meeting": "n_meetings", "call": "n_calls", "email": "n_emails",
                 "note": "n_notes", "task": "n_tasks"}

    def write_deal(deal_id: str, items: List[aiosqlite.Row]) -> None:
        human = [e for e in items if not e["is_automated"]]
        counts = {v: 0 for v in type_keys.values()}
        n_out = n_in = 0
        n_owner_logged = 0
        last_ts = None
        meeting_ts: List[datetime] = []
        n_held = n_booked = 0
        has_open_task = False
        future_meeting = False
        n_since_stage = 0
        owner = deal_owner.get(deal_id)
        stage_entry = cur_entry.get(deal_id)

        for e in human:
            counts[type_keys[e["type"]]] += 1
            ts = parse_ts(e["ts"])
            # "last activity" = most recent PAST sales-meaningful engagement; a
            # future-dated meeting is a next step, and a marketing email blast
            # is not the rep working the deal.
            if (ts is not None and ts <= now and _is_sales_activity(e)
                    and (last_ts is None or ts > last_ts)):
                last_ts = ts
            if e["direction"] == "out":
                n_out += 1
            elif e["direction"] == "in":
                n_in += 1
            if e["owner_id"] and owner and e["owner_id"] == owner:
                n_owner_logged += 1
            if stage_entry is not None and ts is not None and stage_entry <= ts <= now:
                n_since_stage += 1
            if e["type"] == "meeting":
                n_booked += 1
                if ts is not None:
                    meeting_ts.append(ts)
                    if ts > now:
                        future_meeting = True
                if meeting_held(e["status"], ts, now):
                    n_held += 1
            if e["type"] == "task":
                if (e["status"] or "").upper() != "COMPLETED":
                    has_open_task = True

        n_total = sum(counts.values())
        days_since = ((now - last_ts).total_seconds() / 86400.0) if last_ts else None
        first_m = min(meeting_ts).isoformat() if meeting_ts else None
        last_m = max(meeting_ts).isoformat() if meeting_ts else None
        pct_owner = (n_owner_logged / n_total) if n_total else None
        has_next = 1 if (has_open_task or future_meeting) else 0

        return (
            deal_id, last_ts.isoformat() if last_ts else None, days_since,
            counts["n_meetings"], counts["n_calls"], counts["n_emails"],
            counts["n_notes"], counts["n_tasks"], n_total, n_held, n_booked,
            first_m, last_m, n_since_stage, n_out, n_in,
            1 if counts["n_meetings"] else 0, 1 if has_open_task else 0, has_next,
            pct_owner, None, now.isoformat(),
        )

    rows = []
    for deal_id, items in by_deal.items():
        rows.append(write_deal(deal_id, items))
    # open deals with zero engagements -> explicit zero row so "cold" flags fire
    for deal_id in open_ids - set(by_deal.keys()):
        rows.append((deal_id, None, None, 0, 0, 0, 0, 0, 0, 0, 0, None, None, 0,
                     0, 0, 0, 0, 0, None, None, now.isoformat()))

    await conn.executemany(
        """INSERT INTO deal_activity_rollup(
              deal_id, last_activity_ts, days_since_last_activity,
              n_meetings, n_calls, n_emails, n_notes, n_tasks, n_total,
              n_meetings_held, n_meetings_booked, first_meeting_ts, last_meeting_ts,
              n_since_stage_total, n_outbound, n_inbound,
              has_any_meeting, has_open_task, has_next_step,
              pct_activity_logged_by_owner, flags_json, computed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )

    # --- rep weekly activity (human-only, by logging owner) ----------------
    await conn.execute("DELETE FROM rep_week_rollup")
    agg: Dict[tuple, Dict] = defaultdict(lambda: {"acts": 0, "held": 0, "deals": set()})
    for e in all_engs:
        if e["is_automated"]:
            continue
        ts = parse_ts(e["ts"])
        if ts is None:
            continue
        iy, iw = iso_week_key(ts)
        owner = e["owner_id"] or "unassigned"
        a = agg[(owner, iy, iw)]
        a["acts"] += 1
        a["deals"].add(e["deal_id"])
        if e["type"] == "meeting" and meeting_held(e["status"], ts, now):
            a["held"] += 1
    rep_rows = [
        (owner, iy, iw, 0, a["acts"], a["held"], len(a["deals"]), None, 0, 0, 0)
        for (owner, iy, iw), a in agg.items()
    ]
    await conn.executemany(
        """INSERT INTO rep_week_rollup(
              owner_id, iso_year, iso_week, open_deals, activities_logged,
              meetings_held, active_deals_touched, avg_activities_per_open_deal,
              deals_zero_activity, deals_stalled, no_next_step)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        rep_rows,
    )
    return len(rows)
