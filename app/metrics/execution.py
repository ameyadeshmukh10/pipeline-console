"""Report 2 — Pipeline Execution (full pipeline motion, by owner).

EVENT-DATED: any deal that moved in the post-S0 pipeline during the analysis
window contributes its in-window weekly stage events (including deals created
before the window still being worked).
Attributed to the deal's current owner; includes EVERY owner of an S1+ deal.

Three objectives, each poolable by owner + Execution groups:
  1. Entered-stage weekly counts (S1..Lost)
  2. Stage velocity days per transition (S1->S2 .. S5->Won), median AND mean
  3. Funnel conversion per gate: reached N+1-or-beyond / (reached + lost-at-N)
Plus the open-pipeline aging snapshot.
"""
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Tuple

import aiosqlite

from .. import db
from .common import (DealView, disambiguate_first_names, load_deal_views,
                     load_person_names, load_role_sets, normalize_groups,
                     owner_display, partial_week, person_meta, pool_components,
                     pool_value_lists, sum_lists)
from .series import (fit_line, pooled_count_series, pooled_mean_series,
                     pooled_median_series, pooled_rate_series)
from .stats import describe, median
from .windows import (days_between, iso_week_key, iso_weeks_in_range, now_utc,
                      week_label, window_end_dt, window_start_dt)

Week = Tuple[int, int]
UNASSIGNED = "unassigned"

STAGE_SHORTS = {0: "S0", 1: "S1", 2: "S2", 3: "S3", 4: "S4", 5: "S5", 6: "WON", 7: "LOST"}
ENTERED_STAGES = [1, 2, 3, 4, 5, 6, 7]                    # Obj 1 (S1..Lost)
TRANSITIONS = [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]    # Obj 2
GATES = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]  # Obj 3 (S0->S1 incl. for the product)
S1_PLUS = (1, 2, 3, 4, 5, 6, 7)


def owner_key(v: DealView) -> str:
    return v.owner_id or UNASSIGNED


def _in(t, qstart, qend) -> bool:
    return t is not None and qstart <= t <= qend


# --------------------------------------------------------------------------- #
# Objective 1 — entered-stage weekly counts (mirrors prepipeline.compute_created)
# --------------------------------------------------------------------------- #
def compute_entered(deals: List[DealView], weeks: List[Week], stage_order: int,
                    qstart: datetime, qend: datetime, now: datetime, groups=()) -> Dict[str, Any]:
    wkset = set(weeks)
    agg: Dict[Week, int] = {wk: 0 for wk in weeks}
    by: Dict[str, Dict[Week, int]] = defaultdict(lambda: {wk: 0 for wk in weeks})
    for v in deals:
        t = v.entries.get(stage_order)          # ACTUAL entry into this stage (not closure)
        if not _in(t, qstart, qend):            # event must fall inside the window
            continue
        wk = iso_week_key(t)
        if wk not in wkset:
            continue
        agg[wk] += 1
        by[owner_key(v)][wk] += 1
    agg_list = [agg[wk] for wk in weeks]
    by_lists = {pid: [d[wk] for wk in weeks] for pid, d in by.items()}
    complete = [not partial_week(wk, qstart, now) for wk in weeks]
    by_group = {g["id"]: sum_lists([by_lists.get(m) for m in g["member_ids"]], len(weeks))
                for g in groups}

    def cseries(arr):
        return {**pooled_count_series(arr, weeks, complete=complete), "fit": fit_line(arr, weeks, complete)}

    return {
        "aggregate": agg_list, "by_owner": by_lists, "by_group": by_group,
        "series": {
            "aggregate": cseries(agg_list),
            "by_owner": {pid: cseries(a) for pid, a in by_lists.items()},
            "by_group": {gid: cseries(a) for gid, a in by_group.items()},
        },
    }


# --------------------------------------------------------------------------- #
# Objective 2 — stage velocity (days), median AND mean
# --------------------------------------------------------------------------- #
def _to_sum_n(vals_by_week, weeks):
    return {wk: {"sum": float(sum(vals_by_week.get(wk) or [])),
                 "n": len(vals_by_week.get(wk) or [])} for wk in weeks}


def _both_stats(vals_by_week, weeks):
    return {"median": pooled_median_series(vals_by_week, weeks),
            "mean": pooled_mean_series(_to_sum_n(vals_by_week, weeks), weeks)}


def compute_velocity(deals: List[DealView], weeks: List[Week], n: int,
                     qstart: datetime, qend: datetime, groups=()) -> Dict[str, Any]:
    """Transition n -> n+1: days = reach(n+1) - entry(n), bucketed by the week of
    reach(n+1). Raw values kept per (owner, week) so both median and mean (each
    Weekly/4-wk/Cumulative) are exact."""
    wkset = set(weeks)
    agg_vals: Dict[Week, List[float]] = defaultdict(list)
    by: Dict[str, Dict[Week, List[float]]] = defaultdict(lambda: defaultdict(list))
    for v in deals:
        a = v.entries.get(n)
        b = v.first_reach(n + 1)
        if a is None or not _in(b, qstart, qend):   # the transition must land in-window
            continue
        wk = iso_week_key(b)
        if wk not in wkset:
            continue
        d = days_between(a, b)
        if d is None or d < 0:
            continue
        agg_vals[wk].append(d)
        by[owner_key(v)][wk].append(d)
    by_group = {g["id"]: pool_value_lists(by, g["member_ids"], weeks) for g in groups}
    return {
        "aggregate": _both_stats(agg_vals, weeks),
        "by_owner": {pid: _both_stats(vw, weeks) for pid, vw in by.items()},
        "by_group": {gid: _both_stats(vw, weeks) for gid, vw in by_group.items()},
    }


# --------------------------------------------------------------------------- #
# Objective 3 — funnel gate conversion (mirrors prepipeline.compute_conversion)
# --------------------------------------------------------------------------- #
def compute_gate(deals: List[DealView], weeks: List[Week], n: int,
                 qstart: datetime, qend: datetime, groups=()) -> Dict[str, Any]:
    """Gate n -> n+1, cohort by week of entry(n).
    advanced = reach(n+1) (closure-aware, counts won); lost = lost AND not reach(n+1);
    still-open-at-n excluded."""
    wkset = set(weeks)
    agg: Dict[Week, Dict[str, int]] = defaultdict(lambda: {"advanced": 0, "lost": 0})
    by: Dict[str, Dict[Week, Dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"advanced": 0, "lost": 0}))
    for v in deals:
        a = v.entries.get(n)
        if not _in(a, qstart, qend):                 # cohort by in-window entry into stage n
            continue
        wk = iso_week_key(a)
        if wk not in wkset:
            continue
        if v.first_reach(n + 1) is not None:
            cls = "advanced"
        elif v.lost_at is not None:
            cls = "lost"
        else:
            continue
        pid = owner_key(v)
        agg[wk][cls] += 1
        by[pid][wk][cls] += 1
    by_group = {g["id"]: pool_components(by, g["member_ids"], weeks, ("advanced", "lost"))
                for g in groups}
    return {
        "aggregate": pooled_rate_series(agg, weeks),
        "by_owner": {pid: pooled_rate_series(c, weeks) for pid, c in by.items()},
        "by_group": {gid: pooled_rate_series(comp, weeks) for gid, comp in by_group.items()},
    }


def _stat_pair(vals: List[float]) -> Dict[str, Any]:
    return {"median": median(vals), "mean": (sum(vals) / len(vals)) if vals else None, "n": len(vals)}


def velocity_s1_s3_periods(deals: List[DealView], qstart: datetime, qend: datetime) -> Dict[str, Any]:
    """S1->S3 days (entry S1 -> reach S3), split into thirds of the window by the
    week the deal REACHED S3 — so you can see velocity drift across the window.
    (For a continuous window qend is 'now', so thirds cover the window so far.)"""
    total = (qend - qstart).total_seconds() or 1.0
    third_keys = ["first_third", "second_third", "last_third"]
    buckets: Dict[str, List[float]] = {"full": [], "first_third": [], "second_third": [], "last_third": []}
    for v in deals:
        a = v.entries.get(1)
        b = v.first_reach(3)
        if a is None or not _in(b, qstart, qend):
            continue
        d = days_between(a, b)
        if d is None or d < 0:
            continue
        buckets["full"].append(d)
        idx = min(2, max(0, int(((b - qstart).total_seconds() / total) * 3)))
        buckets[third_keys[idx]].append(d)
    return {k: _stat_pair(vals) for k, vals in buckets.items()}


def funnel_summary(deals: List[DealView], qstart: datetime, qend: datetime) -> Dict[str, Any]:
    """Per-gate cumulative conversion over the window cohort + the full-funnel product."""
    gates: List[Dict[str, Any]] = []
    product = 1.0
    have_all = True
    for (n, m) in GATES:
        adv = lost = 0
        for v in deals:
            a = v.entries.get(n)
            if not _in(a, qstart, qend):
                continue
            if v.first_reach(m) is not None:
                adv += 1
            elif v.lost_at is not None:
                lost += 1
        den = adv + lost
        rate = (adv / den) if den else None
        gates.append({"key": f"{STAGE_SHORTS[n]}_{STAGE_SHORTS[m]}",
                      "label": f"{STAGE_SHORTS[n]}→{STAGE_SHORTS[m]}",
                      "advanced": adv, "lost": lost, "denom": den, "rate": rate})
        if rate is None:
            have_all = False
        else:
            product *= rate
    return {"gates": gates, "full_funnel": product if have_all else None}


# --------------------------------------------------------------------------- #
# Open-pipeline aging snapshot
# --------------------------------------------------------------------------- #
def _aging(deals: List[DealView], roles, s: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    s0t = int(s.get("s0_to_s1_target_days", 14))
    s1s3t = int(s.get("s1_to_s3_target_days", 20))
    by_stage = defaultdict(lambda: {"open": 0, "aging": 0, "ages": []})
    rows: List[Dict[str, Any]] = []
    for v in deals:
        if not v.is_open or v.current_order is None:
            continue
        entered = v.entries.get(v.current_order)
        age = days_between(entered, now) if entered else None
        short = STAGE_SHORTS.get(v.current_order, str(v.current_order))
        target = s0t if v.current_order == 0 else (s1s3t if 1 <= v.current_order <= 3 else None)
        slot = by_stage[short]
        slot["open"] += 1
        if age is not None:
            slot["ages"].append(age)
        if target and age is not None and age > target:
            slot["aging"] += 1
            rows.append({"deal_id": v.deal_id, "dealname": v.dealname, "stage": short,
                         "owner": owner_display(v.owner_id, roles), "owner_id": v.owner_id,
                         "age_days": round(age, 1)})
    summary = {st: {"open": d["open"], "aging": d["aging"], "median_age": describe(d["ages"])["median"]}
               for st, d in by_stage.items()}
    return {"summary": summary, "deals": sorted(rows, key=lambda d: -(d["age_days"] or 0))}


# --------------------------------------------------------------------------- #
# Owner roster (anyone owning a deal with S1+ motion in the window) + report
# --------------------------------------------------------------------------- #
def _owner_totals(deals: List[DealView], qstart: datetime, qend: datetime) -> Counter:
    totals: Counter = Counter()
    for v in deals:
        if any(_in(v.entries.get(o), qstart, qend) for o in S1_PLUS):
            totals[owner_key(v)] += 1
    return totals


async def list_owners(conn: aiosqlite.Connection) -> List[Dict[str, Any]]:
    """S1+ owner list for the Execution-group editor."""
    roles = await load_role_sets(conn)
    names = await load_person_names(conn)
    views = await load_deal_views(conn, roster_only=False)
    qstart, qend = window_start_dt(), window_end_dt()
    totals = _owner_totals(list(views.values()), qstart, qend)
    owners = [person_meta(pid, n, names, roles) for pid, n in totals.items()]
    owners.sort(key=lambda c: (-c["total_created"], c["name"]))
    disambiguate_first_names(owners)
    return owners


async def execution_report(conn: aiosqlite.Connection) -> Dict[str, Any]:
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    names = await load_person_names(conn)
    views = await load_deal_views(conn, roster_only=False)
    now = now_utc()
    qstart, qend = window_start_dt(), window_end_dt()
    end_obs = min(now, qend)  # observable end of the window
    weeks = iso_weeks_in_range(qstart, end_obs)
    deals = list(views.values())

    totals = _owner_totals(deals, qstart, qend)
    owners = [person_meta(pid, n, names, roles) for pid, n in totals.items()]
    owners.sort(key=lambda c: (-c["total_created"], c["name"]))
    disambiguate_first_names(owners)
    groups = normalize_groups(await db.get_setting(conn, "execution_groups", []), totals, owners)

    entered = {STAGE_SHORTS[o]: compute_entered(deals, weeks, o, qstart, qend, end_obs, groups)
               for o in ENTERED_STAGES}
    velocity = {f"{STAGE_SHORTS[n]}_{STAGE_SHORTS[m]}": compute_velocity(deals, weeks, n, qstart, qend, groups)
                for (n, m) in TRANSITIONS}
    conversion = {f"{STAGE_SHORTS[n]}_{STAGE_SHORTS[m]}": compute_gate(deals, weeks, n, qstart, qend, groups)
                  for (n, m) in GATES}

    # Fitted trend line for the aggregate velocity (per stat) + conversion lines.
    complete = [not partial_week(wk, qstart, end_obs) for wk in weeks]
    for V in velocity.values():
        for stat in ("median", "mean"):
            V["aggregate"][stat]["fit"] = fit_line([c["avg"] for c in V["aggregate"][stat]["weekly"]], weeks, complete)
    for C in conversion.values():
        C["aggregate"]["fit"] = fit_line(
            [(c["rate"] * 100 if c["rate"] is not None else None) for c in C["aggregate"]["weekly"]], weeks, complete)

    return {
        "weeks": [week_label(*wk) for wk in weeks],
        "owners": owners,
        "groups": groups,
        "entered_stages": [STAGE_SHORTS[o] for o in ENTERED_STAGES],
        "transitions": [{"key": f"{STAGE_SHORTS[n]}_{STAGE_SHORTS[m]}",
                         "label": f"{STAGE_SHORTS[n]}→{STAGE_SHORTS[m]}"} for (n, m) in TRANSITIONS],
        "gates": [{"key": f"{STAGE_SHORTS[n]}_{STAGE_SHORTS[m]}",
                   "label": f"{STAGE_SHORTS[n]}→{STAGE_SHORTS[m]}"} for (n, m) in GATES],
        "entered": entered,
        "entered_totals": {st: sum(E["aggregate"]) for st, E in entered.items()},
        "velocity": velocity,
        "velocity_s1_s3": velocity_s1_s3_periods(deals, qstart, qend),
        "conversion": conversion,
        "funnel_summary": funnel_summary(deals, qstart, qend),
        "aging": _aging(deals, roles, s, now),
        "totals": {"owners": len(owners)},
    }
