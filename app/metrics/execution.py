"""Report 2 — Pipeline Execution (AE motion).

Weekly AE assignment (entered S1), S0->S1->S2->S3 progression funnel, median
velocity per stage, and the distribution-free getting-faster/slower test.
"""
from collections import defaultdict
from typing import Any, Dict, List, Optional

import aiosqlite

from .. import db
from .common import OTHER, load_deal_views, load_role_sets, owner_display
from .stats import describe, trend
from .windows import (days_between, iso_week_key, iso_weeks_in_range, now_utc,
                      quarter_start_dt, week_end_date, week_label, week_start_date)

STAGE_SHORTS = {0: "S0", 1: "S1", 2: "S2", 3: "S3", 4: "S4", 5: "S5", 6: "WON", 7: "LOST"}


async def execution_report(conn: aiosqlite.Connection) -> Dict[str, Any]:
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    views = await load_deal_views(conn)
    now = now_utc()
    qstart = quarter_start_dt()
    s1s3_target = int(s.get("s1_to_s3_target_days", 20))
    s0s1_target = int(s.get("s0_to_s1_target_days", 14))

    weeks = iso_weeks_in_range(qstart, now)
    week_index = {wk: i for i, wk in enumerate(weeks)}

    def ae_attr(oid):
        return oid if oid in roles.ae else OTHER

    # --- weekly AE assignment (entered S1, closure-aware) ------------------
    assign_total = defaultdict(int)
    assign_by_ae: Dict[Any, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ae_keys = set()
    prog = {o: defaultdict(int) for o in (0, 1, 2, 3)}
    s0s1_points: List[tuple] = []
    s1s3_points: List[tuple] = []
    s1s3_by_ae: Dict[str, List[tuple]] = defaultdict(list)
    s0s1_durations: List[float] = []
    s1s3_durations: List[float] = []

    for v in views.values():
        a1 = v.first_reach(1)
        if a1 is not None and a1 >= qstart:
            wk = iso_week_key(a1)
            key = ae_attr(v.owner_id)
            ae_keys.add(key)
            assign_total[wk] += 1
            assign_by_ae[wk][key] += 1

        # progression funnel: forward stage entries within Q2
        for o in (0, 1, 2, 3):
            t = v.entries.get(o)
            if t is not None and t >= qstart:
                prog[o][iso_week_key(t)] += 1

        # velocity legs (completed)
        s0 = v.s0_at
        if s0 is not None and a1 is not None and a1 >= qstart:
            d = days_between(s0, a1)
            if d is not None and d >= 0:
                s0s1_durations.append(d)
                s0s1_points.append((week_index.get(iso_week_key(s0), 0), d))
        a3 = v.first_reach(3)
        if a1 is not None and a3 is not None and a1 >= qstart:
            d = days_between(a1, a3)
            if d is not None and d >= 0:
                s1s3_durations.append(d)
                idx = week_index.get(iso_week_key(a1), 0)
                s1s3_points.append((idx, d))
                s1s3_by_ae[ae_attr(v.owner_id)].append((idx, d))

    ae_keys = sorted(ae_keys, key=lambda k: (k == OTHER, k))
    ae_labels = {k: ("Non-AE owner" if k == OTHER else owner_display(k, roles)) for k in ae_keys}

    weekly_assign: List[Dict[str, Any]] = []
    for wk in weeks:
        partial = week_start_date(*wk) < qstart or week_end_date(*wk) > now
        weekly_assign.append({
            "week": week_label(*wk),
            "total": assign_total.get(wk, 0),
            "by_ae": {k: assign_by_ae.get(wk, {}).get(k, 0) for k in ae_keys},
            "partial": partial,
        })

    funnel_weekly = [{
        "week": week_label(*wk),
        "s0": prog[0].get(wk, 0), "s1": prog[1].get(wk, 0),
        "s2": prog[2].get(wk, 0), "s3": prog[3].get(wk, 0),
    } for wk in weeks]

    # cumulative funnel over the Q2 Stage-0 cohort (closure-aware)
    cohort = [v for v in views.values() if v.s0_at is not None and v.s0_at >= qstart]
    n0 = len(cohort)
    cum = {
        "s0": n0,
        "s1": sum(v.ever_reached(1) for v in cohort),
        "s2": sum(v.ever_reached(2) for v in cohort),
        "s3": sum(v.ever_reached(3) for v in cohort),
        "won": sum(v.won_at is not None for v in cohort),
        "lost": sum(v.lost_at is not None for v in cohort),
    }
    funnel_cumulative = {
        "counts": cum,
        "conv_s0_s1": cum["s1"] / n0 if n0 else None,
        "conv_s1_s2": cum["s2"] / cum["s1"] if cum["s1"] else None,
        "conv_s2_s3": cum["s3"] / cum["s2"] if cum["s2"] else None,
    }

    # completed time-in-stage per stage (Q2 entries, exited)
    stage_dwell: Dict[str, Any] = {}
    rows = await (await conn.execute(
        """SELECT stage_order, dwell_seconds FROM deal_stage_events
           WHERE exited_at IS NOT NULL AND entered_at >= ?""", (qstart.isoformat(),))).fetchall()
    by_stage = defaultdict(list)
    for r in rows:
        by_stage[r["stage_order"]].append(r["dwell_seconds"] / 86400.0)
    for o in range(0, 6):
        stage_dwell[STAGE_SHORTS[o]] = describe(by_stage.get(o, []))

    # velocity legs + trend
    velocity = {
        "s0_s1": describe(s0s1_durations),
        "s1_s3": describe(s1s3_durations),
        "s0_s1_target": s0s1_target,
        "s1_s3_target": s1s3_target,
    }
    net_trend = trend(s1s3_points)
    per_ae_trend = {
        ae_labels[k]: trend(s1s3_by_ae[k]) for k in ae_keys if k in s1s3_by_ae
    }

    # open aging snapshot
    aging_by_stage = defaultdict(lambda: {"open": 0, "aging": 0, "ages": []})
    aging_deals: List[Dict[str, Any]] = []
    for v in views.values():
        if not v.is_open or v.current_order is None:
            continue
        entered = v.entries.get(v.current_order)
        age = days_between(entered, now) if entered else None
        short = STAGE_SHORTS.get(v.current_order, str(v.current_order))
        target = s0s1_target if v.current_order == 0 else (s1s3_target if 1 <= v.current_order <= 3 else None)
        is_aging = bool(target and age is not None and age > target)
        slot = aging_by_stage[short]
        slot["open"] += 1
        if age is not None:
            slot["ages"].append(age)
        if is_aging:
            slot["aging"] += 1
            aging_deals.append({
                "deal_id": v.deal_id, "dealname": v.dealname, "stage": short,
                "owner": owner_display(v.owner_id, roles),
                "age_days": round(age, 1) if age is not None else None,
            })
    aging_summary = {st: {"open": d["open"], "aging": d["aging"],
                          "median_age": describe(d["ages"])["median"]}
                     for st, d in aging_by_stage.items()}

    return {
        "ae_keys": ae_keys,
        "ae_labels": ae_labels,
        "weekly_assign": weekly_assign,
        "funnel_weekly": funnel_weekly,
        "funnel_cumulative": funnel_cumulative,
        "stage_dwell": stage_dwell,
        "velocity": velocity,
        "net_trend": net_trend,
        "per_ae_trend": per_ae_trend,
        "aging_summary": aging_summary,
        "aging_deals": sorted(aging_deals, key=lambda d: -(d["age_days"] or 0)),
        "totals": {"assignments": sum(assign_total.values()), "cohort": n0},
    }
