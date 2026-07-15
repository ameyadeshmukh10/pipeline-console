"""Deterministic flag rule engine. Pure: (DealView, activity rollup, role sets,
settings, now) -> list of flags. Reused by Reports 1/2, the flags drawer, the
forecast packet, and the rep scorecard.
"""
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiosqlite

from .. import db
from .common import DealView, RoleSets, load_deal_views, load_role_sets, owner_display
from .windows import days_between, now_utc

# code -> (label, severity). severity: critical | warning | info
FLAG_META = {
    "STAGE0_AGING":      ("Stage 0 aging past target", "warning"),
    "STAGE0_NON_SDR":    ("Non-SDR owns a Stage 0", "warning"),
    "S1_S3_SLOW":        ("S1→S3 slower than target", "warning"),
    "LATE_STAGE_SILENT": ("Stage 4/5 with no recent activity", "critical"),
    "NICK_OWNS":         ("Nick DiCello owns this deal", "critical"),
    "OWNER_OFF_ROSTER":  ("Owner off-roster (Other/Unknown)", "warning"),
    "NO_AMOUNT":         ("Missing amount on advanced deal", "warning"),
    "CLOSEDATE_STALE":   ("Close date missing or in the past", "warning"),
    "SE_OWNS":           ("SE owns a deal past Stage 0", "warning"),
    "LOST_FROM_S0":      ("Closed-lost directly from Stage 0", "info"),
    "REOPENED":          ("Reopened / back-moved deal", "info"),
}


def _flag(code: str, fact: str) -> Dict[str, Any]:
    label, severity = FLAG_META[code]
    return {"code": code, "label": label, "severity": severity, "fact": fact}


def compute_flags(view: DealView, rollup: Optional[Dict[str, Any]], roles: RoleSets,
                  s: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    oid = view.owner_id
    open_ = bool(view.is_open)
    order = view.current_order

    s0_target = int(s.get("s0_to_s1_target_days", 14))
    s1s3_target = int(s.get("s1_to_s3_target_days", 20))
    late_days = int(s.get("late_stage_activity_days", 7))

    # 1. Stage 0 aging
    if open_ and order == 0 and view.s0_at is not None:
        age = days_between(view.s0_at, now)
        if age is not None and age > s0_target:
            flags.append(_flag("STAGE0_AGING", f"{age:.0f}d in Stage 0 (target {s0_target}d)"))

    # 2. Non-SDR owns a Stage 0
    if open_ and order == 0 and oid not in roles.s0_allowed:
        who = roles.names.get(oid) or (oid or "unassigned")
        flags.append(_flag("STAGE0_NON_SDR", f"Stage 0 owned by non-SDR: {who}"))

    # 3. S1 -> S3 too slow
    s1_at = view.first_reach(1)
    if s1_at is not None:
        s3_at = view.first_reach(3)
        if s3_at is not None:
            d = days_between(s1_at, s3_at)
            if d is not None and d > s1s3_target:
                flags.append(_flag("S1_S3_SLOW", f"{d:.0f}d S1→S3 (target {s1s3_target}d)"))
        elif open_:
            d = days_between(s1_at, now)
            if d is not None and d > s1s3_target:
                flags.append(_flag("S1_S3_SLOW", f"{d:.0f}d since S1, not yet at S3 (target {s1s3_target}d)"))

    # 4. Stage 4/5 stalled (no engagement in N days)
    if open_ and order in (4, 5):
        dsa = rollup.get("days_since_last_activity") if rollup else None
        if dsa is None:
            flags.append(_flag("LATE_STAGE_SILENT", "No logged engagement on a late-stage deal"))
        elif dsa > late_days:
            flags.append(_flag("LATE_STAGE_SILENT", f"No engagement in {dsa:.0f}d (target {late_days}d)"))

    # 5. Nick DiCello owns anything
    if oid in roles.never_owns:
        flags.append(_flag("NICK_OWNS", f"Owned by {roles.names.get(oid, oid)} (should never own)"))

    # 6. Owner off-roster (open deals)
    if open_ and (oid is None or oid not in roles.roster):
        flags.append(_flag("OWNER_OFF_ROSTER", f"Owner {oid or 'unassigned'} not in roster"))

    # 7. Missing amount on advanced deal
    if open_ and order is not None and order >= 3 and not view.amount:
        flags.append(_flag("NO_AMOUNT", "No amount on an S3+ deal — cannot weight forecast"))

    # 8. Close date missing or in the past
    if open_:
        if view.closedate is None:
            flags.append(_flag("CLOSEDATE_STALE", "Open deal has no close date"))
        elif view.closedate < now:
            flags.append(_flag("CLOSEDATE_STALE", f"Close date {view.closedate.date()} is in the past"))

    # 9. SE owns past Stage 0
    if open_ and oid in roles.se and order is not None and order >= 1:
        flags.append(_flag("SE_OWNS", f"SE {roles.names.get(oid, oid)} owns an S{order} deal"))

    # 10. Closed-lost directly from Stage 0 (never reached S1)
    if view.lost_at is not None and not view.ever_reached(1):
        flags.append(_flag("LOST_FROM_S0", "Lost from Stage 0 without AE handoff"))

    # 11. Reopened / back-moved (non-monotonic entry order over time)
    if _has_back_movement(view):
        flags.append(_flag("REOPENED", "Stage order moved backward over time (reopen/regression)"))

    return flags


def _has_back_movement(view: DealView) -> bool:
    ordered = sorted(view.entries.items(), key=lambda kv: kv[1])  # by time
    seen_max = -1
    for order, _ts in ordered:
        if order < seen_max:
            return True
        seen_max = max(seen_max, order)
    return False


async def compute_all_flags(conn: aiosqlite.Connection, now: Optional[datetime] = None) -> Dict[str, Any]:
    """All active flags across deals + a summary, for the flags drawer/API."""
    from .windows import window_end_dt, window_start_dt
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    views = await load_deal_views(conn)
    now = now or now_utc()
    qstart, qend = window_start_dt(), window_end_dt()
    rollups = {r["deal_id"]: dict(r) for r in
               await (await conn.execute("SELECT * FROM deal_activity_rollup")).fetchall()}

    out: List[Dict[str, Any]] = []
    by_code: Dict[str, int] = defaultdict(int)
    by_sev: Dict[str, int] = defaultdict(int)
    for v in views.values():
        # Scope flags to the analysis window: open deals + deals closed in it.
        if not v.is_open and not (v.closedate is not None and qstart <= v.closedate <= qend):
            continue
        fl = compute_flags(v, rollups.get(v.deal_id), roles, s, now)
        if not fl:
            continue
        for f in fl:
            by_code[f["code"]] += 1
            by_sev[f["severity"]] += 1
        out.append({
            "deal_id": v.deal_id, "dealname": v.dealname,
            "owner": owner_display(v.owner_id, roles), "owner_id": v.owner_id,
            "stage": v.dealstage, "stage_order": v.current_order, "is_open": v.is_open,
            "flags": fl,
        })
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    out.sort(key=lambda d: min(sev_rank.get(f["severity"], 3) for f in d["flags"]))
    return {"deals": out, "by_code": dict(by_code), "by_severity": dict(by_sev),
            "total_flagged": len(out), "flag_meta": FLAG_META}
