"""Report 1 — Pre-Pipeline Generation (SDR quality).

Reworked: scoped to deals whose CREATEDATE falls in the quarter, attributed by
the true CREATOR (hs_created_by_user_id), and includes EVERY creator (roster,
off-roster, archived ex-reps). Three deterministic, unit-tested views:

  1. Weekly Stage-0 created   — count per creator per createdate-week (+ trend)
  2. Weekly Speed to Stage 1  — avg days S0->S1, bucketed by the S1-transition
                                week, per creator (weekly / 4-wk roll / cumulative)
  3. Weekly S0->S1 conversion — advanced / (advanced + closed-lost-at-S0),
                                EXCLUDING still-open S0 deals (the trust fix)

All math lives in the pure functions below so it is testable without a DB.
"""
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from .. import db
from ..constants import ROSTER_BY_ID
from .common import DealView, load_deal_views, load_role_sets
from .series import (fit_line, pooled_count_series, pooled_mean_series,
                     pooled_rate_series)
from .stats import trend
from .windows import (days_between, iso_week_key, iso_weeks_in_range, now_utc,
                      quarter_end_dt, quarter_start_dt, week_end_date,
                      week_label, week_start_date)

Week = Tuple[int, int]
UNKNOWN = "unknown"


def creator_key(v: DealView) -> str:
    return v.created_by or UNKNOWN


def in_quarter(v: DealView, qstart: datetime, qend: datetime) -> bool:
    return v.createdate is not None and qstart <= v.createdate <= qend


# --------------------------------------------------------------------------- #
# Pure computation (operate on a list of DealViews already scoped to the quarter)
# --------------------------------------------------------------------------- #
def compute_created(deals: List[DealView], weeks: List[Week],
                    qstart: datetime, now: datetime, groups=()) -> Dict[str, Any]:
    """Weekly Stage-0 created, bucketed by createdate-week & creator.

    Returns raw counts (aggregate/by_creator/by_group, used by the tables) AND a
    `series` block with weekly/4-wk-rolling/cumulative/pace + a fitted trend line
    for the aggregate, each creator, and each group.
    """
    wkset = set(weeks)
    agg: Dict[Week, int] = {wk: 0 for wk in weeks}
    by: Dict[str, Dict[Week, int]] = defaultdict(lambda: {wk: 0 for wk in weeks})
    for v in deals:
        wk = iso_week_key(v.createdate)
        if wk not in wkset:
            continue
        agg[wk] += 1
        by[creator_key(v)][wk] += 1
    agg_list = [agg[wk] for wk in weeks]
    by_lists = {cid: [d[wk] for wk in weeks] for cid, d in by.items()}
    complete = [not _partial(wk, qstart, now) for wk in weeks]
    by_group = {g["id"]: _sum_lists([by_lists.get(m) for m in g["member_ids"]], len(weeks))
                for g in groups}

    def cseries(arr: List[int]) -> Dict[str, Any]:
        return {**pooled_count_series(arr, weeks, complete=complete),
                "fit": fit_line(arr, weeks, complete)}

    pts = [(i, agg_list[i]) for i in range(len(weeks)) if complete[i]]
    return {
        "aggregate": agg_list,
        "by_creator": by_lists,
        "by_group": by_group,
        "trend": trend(pts, min_n=4, metric="count"),
        "series": {
            "aggregate": cseries(agg_list),
            "by_creator": {cid: cseries(arr) for cid, arr in by_lists.items()},
            "by_group": {gid: cseries(arr) for gid, arr in by_group.items()},
        },
    }


def compute_speed_to_s1(deals: List[DealView], weeks: List[Week], groups=()) -> Dict[str, Any]:
    """Avg days S0->S1, bucketed by the week the deal ENTERED S1, per creator."""
    wkset = set(weeks)
    agg: Dict[Week, Dict[str, float]] = defaultdict(lambda: {"sum": 0.0, "n": 0})
    by: Dict[str, Dict[Week, Dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"sum": 0.0, "n": 0}))
    for v in deals:
        s1 = v.first_reach(1)
        if s1 is None:
            continue
        wk = iso_week_key(s1)
        if wk not in wkset:
            continue
        start = v.s0_at or v.createdate
        days = days_between(start, s1)
        if days is None:
            continue
        cid = creator_key(v)
        agg[wk]["sum"] += days
        agg[wk]["n"] += 1
        by[cid][wk]["sum"] += days
        by[cid][wk]["n"] += 1
    by_group = {g["id"]: _pool_components(by, g["member_ids"], weeks, ("sum", "n"))
                for g in groups}
    return {
        "aggregate": pooled_mean_series(agg, weeks),
        "by_creator": {cid: pooled_mean_series(c, weeks) for cid, c in by.items()},
        "by_group": {gid: pooled_mean_series(comp, weeks) for gid, comp in by_group.items()},
    }


def compute_conversion(deals: List[DealView], weeks: List[Week], groups=()) -> Dict[str, Any]:
    """Corrected S0->S1 conversion, cohorted by createdate-week.

    advanced     = ever reached S1+ (closure-aware; counts even if now closed/won)
    lost-at-S0   = closed-lost AND never reached S1
    open-S0      = EXCLUDED (not yet resolved — never counted either way)
    rate         = advanced / (advanced + lost-at-S0)
    """
    wkset = set(weeks)
    agg: Dict[Week, Dict[str, int]] = defaultdict(lambda: {"advanced": 0, "lost": 0})
    by: Dict[str, Dict[Week, Dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"advanced": 0, "lost": 0}))
    for v in deals:
        wk = iso_week_key(v.createdate)
        if wk not in wkset:
            continue
        if v.ever_reached(1):
            cls = "advanced"
        elif v.lost_at is not None:
            cls = "lost"
        else:
            continue  # still open in Stage 0 -> not resolved -> excluded
        cid = creator_key(v)
        agg[wk][cls] += 1
        by[cid][wk][cls] += 1
    by_group = {g["id"]: _pool_components(by, g["member_ids"], weeks, ("advanced", "lost"))
                for g in groups}
    return {
        "aggregate": pooled_rate_series(agg, weeks),
        "by_creator": {cid: pooled_rate_series(c, weeks) for cid, c in by.items()},
        "by_group": {gid: pooled_rate_series(comp, weeks) for gid, comp in by_group.items()},
    }


# --------------------------------------------------------------------------- #
# Group pooling helpers (pure)
# --------------------------------------------------------------------------- #
def _sum_lists(arrs, n: int) -> List[int]:
    out = [0] * n
    for arr in arrs:
        if not arr:
            continue
        for i in range(min(n, len(arr))):
            out[i] += arr[i] or 0
    return out


def _pool_components(by, member_ids, weeks, fields):
    """Sum the per-creator weekly component dicts for a group's members."""
    out = {wk: {f: 0 for f in fields} for wk in weeks}
    for cid in member_ids:
        cdict = by.get(cid)
        if not cdict:
            continue
        for wk, comp in cdict.items():
            if wk not in out:
                continue
            for f in fields:
                out[wk][f] += comp.get(f, 0)
    return out


def _normalize_groups(raw, totals, creators):
    """Validate settings.creator_groups + resolve member names/totals."""
    first_by_id = {c["id"]: c["first_name"] for c in creators if c["id"]}
    full_by_id = {c["id"]: c["name"] for c in creators if c["id"]}
    out = []
    seen = set()
    for g in raw or []:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or g.get("name") or "").strip()
        name = str(g.get("name") or gid).strip()
        members = [str(m) for m in (g.get("member_ids") or []) if m]
        if not gid or gid in seen or not members:
            continue
        seen.add(gid)
        out.append({
            "id": gid, "name": name, "member_ids": members,
            "member_names": [full_by_id.get(m) or first_by_id.get(m) or f"Other ({m})" for m in members],
            "total_created": sum(totals.get(m, 0) for m in members),
        })
    return out


def _partial(wk: Week, qstart: datetime, now: datetime) -> bool:
    return week_start_date(*wk) < qstart or week_end_date(*wk) > now


# --------------------------------------------------------------------------- #
# DB wrapper + name resolution
# --------------------------------------------------------------------------- #
async def _load_owner_names(conn: aiosqlite.Connection) -> Dict[str, Dict[str, Any]]:
    rows = await (await conn.execute(
        "SELECT owner_id, first_name, last_name, archived FROM owners")).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        first = (r["first_name"] or "").strip()
        last = (r["last_name"] or "").strip()
        out[r["owner_id"]] = {
            "first": first, "last": last,
            "full": (first + " " + last).strip(),
            "archived": bool(r["archived"]),
        }
    return out


def _creator_meta(cid: str, total: int, names: Dict[str, Dict[str, Any]],
                  roles) -> Dict[str, Any]:
    if cid == UNKNOWN:
        return {"id": None, "first_name": "Unknown", "name": "Unknown", "last": "",
                "in_roster": False, "role": "", "archived": False, "total_created": total}
    nm = names.get(cid)
    if nm and nm["full"]:
        first, last, full, archived = nm["first"] or nm["full"], nm["last"], nm["full"], nm["archived"]
    elif cid in roles.names:
        full = roles.names[cid]
        first, last, archived = full.split()[0], "", False
    elif cid in ROSTER_BY_ID:
        full = ROSTER_BY_ID[cid]["display_name"]
        first, last, archived = full.split()[0], "", False
    else:
        first = last = full = f"Other ({cid})"
        archived = False
    role = ("SDR" if cid in roles.sdr else "AE" if cid in roles.ae
            else "SE" if cid in roles.se else "never" if cid in roles.never_owns else "")
    return {"id": cid, "first_name": first, "name": full, "last": last,
            "in_roster": cid in roles.roster, "role": role,
            "archived": archived, "total_created": total}


def _disambiguate(creators: List[Dict[str, Any]]) -> None:
    counts = Counter(c["first_name"] for c in creators)
    for c in creators:
        if counts[c["first_name"]] > 1 and c.get("last"):
            c["first_name"] = f"{c['first_name']} {c['last'][0]}."


async def pre_pipeline_report(conn: aiosqlite.Connection) -> Dict[str, Any]:
    roles = await load_role_sets(conn)
    names = await _load_owner_names(conn)
    # roster_only=False -> include EVERY creator (off-roster + archived ex-reps)
    views = await load_deal_views(conn, roster_only=False, by="creator")

    now = now_utc()
    qstart, qend = quarter_start_dt(), quarter_end_dt()
    weeks = iso_weeks_in_range(qstart, min(now, qend))
    q_deals = [v for v in views.values() if in_quarter(v, qstart, qend)]

    totals = Counter(creator_key(v) for v in q_deals)
    creators = [_creator_meta(cid, n, names, roles) for cid, n in totals.items()]
    creators.sort(key=lambda c: (-c["total_created"], c["name"]))
    _disambiguate(creators)

    groups = _normalize_groups(await db.get_setting(conn, "creator_groups", []), totals, creators)

    created = compute_created(q_deals, weeks, qstart, now, groups)
    speed = compute_speed_to_s1(q_deals, weeks, groups)
    conversion = compute_conversion(q_deals, weeks, groups)

    return {
        "quarter_start": qstart.isoformat(),
        "quarter_end": qend.isoformat(),
        "weeks": [week_label(*wk) for wk in weeks],
        "creators": creators,
        "groups": groups,
        "created": created,
        "speed": speed,
        "conversion": conversion,
        "totals": {"stage0_created": len(q_deals), "creators_count": len(creators)},
    }


async def list_creators(conn: aiosqlite.Connection) -> List[Dict[str, Any]]:
    """Lightweight creator list (id, first_name, name, ...) for the group editor."""
    roles = await load_role_sets(conn)
    names = await _load_owner_names(conn)
    views = await load_deal_views(conn, roster_only=False, by="creator")
    qstart, qend = quarter_start_dt(), quarter_end_dt()
    totals = Counter(creator_key(v) for v in views.values() if in_quarter(v, qstart, qend))
    creators = [_creator_meta(cid, n, names, roles) for cid, n in totals.items()]
    creators.sort(key=lambda c: (-c["total_created"], c["name"]))
    _disambiguate(creators)
    return creators
