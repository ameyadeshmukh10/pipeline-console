"""Report 5 — Deal cohort analytics, cohorted by CREATE week (calendar/ISO).

For each create-week vintage: stage-progression funnel, stage depth, conversion
rates (0->1, 1->2, 2->3, 3->4, 4->5, 0->lost) and median time between stages.
Closure-aware (reaching S_k implies all earlier stages passed).
"""
from collections import defaultdict
from typing import Any, Dict, List

import aiosqlite

from .common import load_deal_views
from .stats import median, trend
from .windows import (days_between, is_mature, iso_weeks_in_range, now_utc,
                      quarter_start_dt, week_label)

# transitions we time, as (from_order, to_order)
LEGS = [("s0_s1", 0, 1), ("s1_s2", 1, 2), ("s2_s3", 2, 3), ("s3_s4", 3, 4), ("s4_s5", 4, 5)]
STAGE_KEYS = [("s0", 0), ("s1", 1), ("s2", 2), ("s3", 3), ("s4", 4), ("s5", 5)]


def _blank() -> Dict[str, Any]:
    return {"n": 0,
            "reached": {k: 0 for k, _ in STAGE_KEYS} | {"won": 0, "lost": 0},
            "depth_dist": defaultdict(int),
            "lost_from_s0": 0,
            "legs": defaultdict(list)}


def _summarize(acc: Dict[str, Any], week: str, mature: bool) -> Dict[str, Any]:
    n = acc["n"]
    reached = acc["reached"]

    def pct(a, b):
        return round(a / b, 3) if b else None

    depth_vals: List[int] = []
    for order, cnt in acc["depth_dist"].items():
        depth_vals.extend([order] * cnt)

    return {
        "week": week, "n": n, "mature": mature,
        "reached": reached,
        "reached_pct": {k: pct(reached[k], n) for k in reached},
        "conv": {
            "s0_s1": pct(reached["s1"], reached["s0"]),
            "s1_s2": pct(reached["s2"], reached["s1"]),
            "s2_s3": pct(reached["s3"], reached["s2"]),
            "s3_s4": pct(reached["s4"], reached["s3"]),
            "s4_s5": pct(reached["s5"], reached["s4"]),
            "s0_lost": pct(reached["lost"], n),
        },
        "lost_from_s0": acc["lost_from_s0"],
        "lost_from_s0_rate": pct(acc["lost_from_s0"], n),
        "depth": {"median": median(depth_vals),
                  "dist": {str(o): acc["depth_dist"].get(o, 0) for o in range(0, 7)}},
        "timing": {key: {"median": median(acc["legs"][key]), "n": len(acc["legs"][key])}
                   for key, _f, _t in LEGS},
    }


async def cohort_report(conn: aiosqlite.Connection) -> Dict[str, Any]:
    views = await load_deal_views(conn)
    now = now_utc()
    qstart = quarter_start_dt()
    weeks = iso_weeks_in_range(qstart, now)

    by_week: Dict[Any, Dict[str, Any]] = {wk: _blank() for wk in weeks}
    totals = _blank()

    for v in views.values():
        if v.createdate is None or v.createdate < qstart:
            continue
        wk = (v.create_iso_year, v.create_iso_week)
        acc = by_week.get(wk)
        if acc is None:
            acc = by_week[wk] = _blank()

        for target in (acc, totals):
            target["n"] += 1
            target["reached"]["s0"] += 1
            for key, order in STAGE_KEYS[1:]:
                if v.ever_reached(order):
                    target["reached"][key] += 1
            if v.won_at is not None:
                target["reached"]["won"] += 1
            if v.lost_at is not None:
                target["reached"]["lost"] += 1
                if not v.ever_reached(1):
                    target["lost_from_s0"] += 1
            target["depth_dist"][v.max_pipeline_order] += 1
            for key, fo, to in LEGS:
                a = v.entries.get(fo) if fo == 0 else v.first_reach(fo)
                b = v.first_reach(to)
                if a is not None and b is not None:
                    d = days_between(a, b)
                    if d is not None and d >= 0:
                        target["legs"][key].append(d)

    cohorts = [_summarize(by_week[wk], week_label(*wk), is_mature(wk[0], wk[1], 14, now))
               for wk in weeks]

    # Did conversion lift as the quarter progressed? Trend across cohort weeks
    # (only cohorts with enough deals to be meaningful).
    def conv_trend(field: str) -> Dict[str, Any]:
        # Mature cohorts only — recent weeks haven't had time to convert, which
        # would otherwise fake a downward trend (right-censoring).
        pts = [(i, c["conv"][field]) for i, c in enumerate(cohorts)
               if c["mature"] and c["n"] >= 3 and c["conv"][field] is not None]
        t = trend(pts, min_n=4, metric="count")
        first = pts[0][1] if pts else None
        last = pts[-1][1] if pts else None
        return {**t, "first": first, "last": last,
                "delta": (last - first) if (first is not None and last is not None) else None}

    return {"cohorts": cohorts, "totals": _summarize(totals, "All Q2", True),
            "stage_keys": [k for k, _ in STAGE_KEYS], "legs": [k for k, _f, _t in LEGS],
            "conv_trends": {"s0_s1": conv_trend("s0_s1"), "s0_lost": conv_trend("s0_lost")}}
