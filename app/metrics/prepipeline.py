"""Report 1 — Pre-Pipeline Generation (SDR quality).

Event-dated by Stage-0 entry; ISO-week cohorts; cohort-maturation conversion
with right-censoring of immature recent weeks.
"""
from collections import defaultdict
from typing import Any, Dict, List

import aiosqlite

from .. import db
from .common import OTHER, load_deal_views, load_role_sets, owner_display
from .stats import median, trend
from .windows import (days_between, is_mature, iso_week_key, iso_weeks_in_range,
                      now_utc, quarter_start_dt, week_end_date, week_label,
                      week_start_date)


async def pre_pipeline_report(conn: aiosqlite.Connection) -> Dict[str, Any]:
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    # SDR-creation quality is about who CREATED the deal, not who owns it now —
    # scope and attribute by the true creator (hs_created_by_user_id).
    views = await load_deal_views(conn, by="creator")
    now = now_utc()
    qstart = quarter_start_dt()
    horizon = int(s.get("s0_to_s1_target_days", 14))

    weeks = iso_weeks_in_range(qstart, now)
    week_index = {wk: i for i, wk in enumerate(weeks)}

    def s0_attr(creator):
        return creator if creator in roles.s0_allowed else OTHER

    # cohort of Stage-0 entries within Q2
    cohort: List[Dict[str, Any]] = []
    weekly_total = defaultdict(int)
    weekly_by_sdr: Dict[Any, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sdr_keys = set()

    for v in views.values():
        s0 = v.s0_at
        if s0 is None or s0 < qstart:
            continue
        wk = iso_week_key(s0)
        key = s0_attr(v.created_by)
        sdr_keys.add(key)
        weekly_total[wk] += 1
        weekly_by_sdr[wk][key] += 1

        s1_at = v.first_reach(1)
        lost_at = v.lost_at
        cohort.append({
            "deal_id": v.deal_id, "wk": wk, "sdr": key, "owner_id": v.owner_id,
            "s1_within": s1_at is not None and (days_between(s0, s1_at) or 1e9) <= horizon,
            "ever_s1": s1_at is not None,
            "days_s0_s1": days_between(s0, s1_at) if s1_at is not None else None,
            "lost_within": lost_at is not None and (days_between(s0, lost_at) or 1e9) <= horizon,
            "lost_from_s0": lost_at is not None and not v.ever_reached(1),
            "open_s0": bool(v.is_open) and v.current_order == 0,
            "open_s0_aging": bool(v.is_open) and v.current_order == 0
                              and (days_between(s0, now) or 0) > horizon,
        })

    sdr_keys = sorted(sdr_keys, key=lambda k: (k == OTHER, k))
    sdr_labels = {k: ("AE-created (not SDR)" if k == OTHER else owner_display(k, roles)) for k in sdr_keys}

    # weekly creation series + WoW
    weekly: List[Dict[str, Any]] = []
    for wk in weeks:
        partial = week_start_date(*wk) < qstart or week_end_date(*wk) > now
        weekly.append({
            "week": week_label(*wk),
            "total": weekly_total.get(wk, 0),
            "by_sdr": {k: weekly_by_sdr.get(wk, {}).get(k, 0) for k in sdr_keys},
            "partial": partial,
        })
    for i, row in enumerate(weekly):
        prev = weekly[i - 1]["total"] if i > 0 else None
        row["wow_delta"] = (row["total"] - prev) if prev is not None else None
        row["wow_pct"] = ((row["total"] - prev) / prev * 100.0) if prev else None
    net_trend = trend([(i, r["total"]) for i, r in enumerate(weekly) if not r["partial"]],
                      min_n=4, metric="count")

    # conversion cohorts
    conv_agg: Dict[Any, Dict[str, int]] = defaultdict(
        lambda: {"n": 0, "s1_within": 0, "ever_s1": 0, "lost_within": 0, "lost_from_s0": 0})
    for c in cohort:
        a = conv_agg[c["wk"]]
        a["n"] += 1
        a["s1_within"] += int(c["s1_within"])
        a["ever_s1"] += int(c["ever_s1"])
        a["lost_within"] += int(c["lost_within"])
        a["lost_from_s0"] += int(c["lost_from_s0"])

    conversion: List[Dict[str, Any]] = []
    for wk in weeks:
        a = conv_agg.get(wk, {"n": 0, "s1_within": 0, "ever_s1": 0, "lost_within": 0, "lost_from_s0": 0})
        n = a["n"]
        mature = is_mature(wk[0], wk[1], horizon, now)
        conversion.append({
            "week": week_label(*wk), "cohort_n": n, "mature": mature,
            "s0_s1_within": a["s1_within"],
            "s0_s1_rate": (a["s1_within"] / n) if n else None,
            "s0_s1_ever_rate": (a["ever_s1"] / n) if n else None,
            "s0_lost_within": a["lost_within"],
            "s0_lost_rate": (a["lost_within"] / n) if n else None,
            "lost_from_s0": a["lost_from_s0"],
            "lost_from_s0_rate": (a["lost_from_s0"] / n) if n else None,
        })

    # per-SDR comparison (Q2 aggregate for stability)
    per_sdr: List[Dict[str, Any]] = []
    for key in sdr_keys:
        deals = [c for c in cohort if c["sdr"] == key]
        n = len(deals)
        ever = sum(c["ever_s1"] for c in deals)
        lost0 = sum(c["lost_from_s0"] for c in deals)
        conv_days = [c["days_s0_s1"] for c in deals if c["days_s0_s1"] is not None]
        aging = sum(c["open_s0_aging"] for c in deals)
        open_s0 = sum(c["open_s0"] for c in deals)
        per_sdr.append({
            "owner_id": None if key == OTHER else key,
            "name": sdr_labels[key],
            "total_s0": n,
            "s0_s1_ever_rate": (ever / n) if n else None,
            "lost_from_s0_rate": (lost0 / n) if n else None,
            "median_days_s0_s1": median(conv_days),
            "open_s0_aging": aging,
            "open_s0": open_s0,
        })

    return {
        "horizon_days": horizon,
        "sdr_keys": sdr_keys,
        "sdr_labels": sdr_labels,
        "weekly": weekly,
        "net_trend": net_trend,
        "conversion": conversion,
        "per_sdr": per_sdr,
        "totals": {
            "stage0_created": sum(weekly_total.values()),
            "cohort_deals": len(cohort),
        },
    }
