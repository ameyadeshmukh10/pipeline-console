"""Report 3 (Part A) — deterministic weighted-$ forecast.

All dollars, probabilities, coverage and gap are computed HERE in Python and
frozen into a per-AE forecast packet. The LLM agent (Part B) only reasons over
this packet; it never recomputes a number.
"""
import json
import uuid
from typing import Any, Dict, List, Optional

import aiosqlite

from .. import db
from .common import load_deal_views, load_role_sets, owner_display
from .flags import compute_flags
from .windows import (days_between, now_utc, quarter_end_dt, quarter_start_dt)

LATE_STAGE_ORDERS = {4, 5}


def _scenario_bucket(order: Optional[int], active: bool) -> str:
    """Which forecast bucket a deal falls in (deterministic, by stage+activity)."""
    if order in (4, 5):
        return "commit" if active else "best"
    if order == 3:
        return "best"
    return "upside"  # S1, S2


def _p_win(view, stage_probs: Dict[str, float], source: str) -> float:
    house = float(stage_probs.get(view.dealstage, 0.0))
    if source == "hubspot" and view.hs_prob is not None:
        return float(view.hs_prob)
    if source == "blend" and view.hs_prob is not None:
        return (house + float(view.hs_prob)) / 2.0
    return house


async def build_forecast_packet(conn: aiosqlite.Connection, owner_id: str,
                                target: Optional[float] = None) -> Dict[str, Any]:
    s = await db.get_all_settings(conn)
    roles = await load_role_sets(conn)
    views = await load_deal_views(conn)
    now = now_utc()
    qstart, qend = quarter_start_dt(), quarter_end_dt()
    stage_probs = s.get("stage_probabilities", {})
    source = s.get("probability_source", "house")

    rollups = {r["deal_id"]: dict(r) for r in
               await (await conn.execute("SELECT * FROM deal_activity_rollup")).fetchall()}

    if target is None:
        ae_targets = s.get("ae_targets", {}) or {}
        target = float(ae_targets.get(owner_id) or s.get("org_target") or 0) or 0.0
    target_source = "request" if target else "settings"

    late_days = int(s.get("late_stage_activity_days", 7))
    mine = [v for v in views.values() if v.owner_id == owner_id]
    open_pipe = [v for v in mine if v.is_open and v.current_order in (1, 2, 3, 4, 5)]
    early_s0 = [v for v in mine if v.is_open and v.current_order == 0]

    deals: List[Dict[str, Any]] = []
    for v in open_pipe:
        roll = rollups.get(v.deal_id)
        entered = v.entries.get(v.current_order)
        s1_at = v.first_reach(1)
        p = _p_win(v, stage_probs, source)
        amt = v.amount or 0.0
        flags = compute_flags(v, roll, roles, s, now)
        dsa = roll.get("days_since_last_activity") if roll else None
        active = dsa is not None and dsa <= late_days
        deals.append({
            "deal_id": v.deal_id, "dealname": v.dealname,
            "stage_id": v.dealstage, "stage_order": v.current_order,
            "amount": v.amount, "p_win": round(p, 3), "weighted_amount": round(amt * p, 2),
            "hubspot_probability": v.hs_prob,
            "closedate": v.closedate.isoformat() if v.closedate else None,
            "closes_in_quarter": v.closedate is not None and v.closedate <= qend,
            "days_in_current_stage": round(days_between(entered, now), 1) if entered else None,
            "age_in_pipeline_days": round(days_between(s1_at, now), 1) if s1_at else None,
            "last_engagement_at": roll.get("last_activity_ts") if roll else None,
            "days_since_engagement": round(dsa, 1) if dsa is not None else None,
            "is_active": active,
            "scenario": _scenario_bucket(v.current_order, active),
            "flags": flags,
        })

    # Monotonic scenario ladder: worst <= commit, most_likely <= best.
    # (We do NOT gate on closedate-in-quarter — most of this pipeline closes
    #  next quarter, and gating would zero the scenarios. Close date is shown
    #  per deal instead.)
    by = {d["deal_id"]: d for d in deals}
    amt_of = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    wtd_of = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    active_late = 0.0  # full amount of actively-worked S4/S5
    active_s5 = 0.0    # full amount of actively-worked S5
    for v in open_pipe:
        o = v.current_order
        amt = v.amount or 0.0
        amt_of[o] += amt
        wtd_of[o] += by[v.deal_id]["weighted_amount"]
        if by[v.deal_id]["is_active"] and o in (4, 5):
            active_late += amt
            if o == 5:
                active_s5 += amt

    closed_won_qtd = sum((v.amount or 0.0) for v in mine
                         if v.won_at is not None and v.won_at >= qstart)
    weighted_sum = sum(d["weighted_amount"] for d in deals)
    open_pipeline = sum((v.amount or 0.0) for v in open_pipe)

    weighted_early = wtd_of[1] + wtd_of[2]
    full_late = amt_of[3] + amt_of[4] + amt_of[5]

    worst_case = closed_won_qtd + active_s5                       # only contracts sent & moving
    commit = closed_won_qtd + active_late                        # late-stage actively worked
    most_likely = closed_won_qtd + weighted_sum                  # probability-weighted expectation
    best_case = closed_won_qtd + weighted_early + full_late      # all Proposal+ land at full value

    coverage = (open_pipeline / target) if target else None
    weighted_coverage = (most_likely / target) if target else None
    gap = (target - most_likely) if target else None
    if coverage is None:
        health = "no_target"
    elif coverage >= 3:
        health = "healthy"
    elif coverage >= 2:
        health = "watch"
    else:
        health = "thin"

    return {
        "ae": {"id": owner_id, "name": owner_display(owner_id, roles),
               "target": target, "target_source": target_source},
        "scenarios": {
            "closed_won_qtd": round(closed_won_qtd, 2),
            "commit": round(commit, 2),
            "most_likely": round(most_likely, 2),
            "best_case": round(best_case, 2),
            "worst_case": round(worst_case, 2),
            "weighted_open": round(weighted_sum, 2),
        },
        "coverage": {
            "open_pipeline": round(open_pipeline, 2),
            "pipeline_coverage": round(coverage, 2) if coverage is not None else None,
            "weighted_coverage": round(weighted_coverage, 2) if weighted_coverage is not None else None,
            "gap_to_target": round(gap, 2) if gap is not None else None,
            "health": health,
        },
        "deals": sorted(deals, key=lambda d: -(d["weighted_amount"] or 0)),
        "early_pipeline_count": len(early_s0),
        "probability_source": source,
        "data_gaps": [d["deal_id"] for d in deals
                      if any(f["code"] in ("NO_AMOUNT", "OWNER_OFF_ROSTER") for f in d["flags"])],
    }


async def store_forecast_run(conn: aiosqlite.Connection, packet: Dict[str, Any],
                             narrative: Dict[str, Any]) -> str:
    run_id = uuid.uuid4().hex
    sc = packet["scenarios"]
    cov = packet["coverage"]
    ae = packet["ae"]
    now = now_utc().isoformat()
    await conn.execute(
        """INSERT INTO forecast_runs(run_id, scope, created_at, closed_won_qtd, commit_amount,
              most_likely_amount, best_case_amount, worst_case_amount, weighted_amount,
              open_pipeline, pipeline_coverage, weighted_coverage, target_amount, gap_to_target,
              model, narrative_json, input_snapshot)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, f"ae:{ae['id']}", now, sc["closed_won_qtd"], sc["commit"], sc["most_likely"],
         sc["best_case"], sc["worst_case"], sc["weighted_open"], cov["open_pipeline"],
         cov["pipeline_coverage"], cov["weighted_coverage"], ae["target"], cov["gap_to_target"],
         narrative.get("model"), json.dumps(narrative), json.dumps(packet, default=str)))

    weighted_by_id = {d["deal_id"]: d["weighted_amount"] for d in packet["deals"]}
    items = []
    for d in narrative.get("deals_to_discuss", []):
        items.append((run_id, d["deal_id"], "discuss", weighted_by_id.get(d["deal_id"]),
                      d.get("reason"), json.dumps(d)))
    for d in narrative.get("anomalies", []):
        items.append((run_id, d["deal_id"], "anomaly", weighted_by_id.get(d["deal_id"]),
                      d.get("issue"), json.dumps(d)))
    for d in narrative.get("should_be_closed_lost", []):
        items.append((run_id, d["deal_id"], "close_lost_candidate", weighted_by_id.get(d["deal_id"]),
                      d.get("rationale"), json.dumps(d)))
    if items:
        await conn.executemany(
            """INSERT INTO forecast_deal_items(run_id, deal_id, category, weighted_amount, rationale, flags)
               VALUES(?,?,?,?,?,?)""", items)
    await conn.commit()
    return run_id


async def latest_forecast_run(conn: aiosqlite.Connection, owner_id: str) -> Optional[Dict[str, Any]]:
    row = await (await conn.execute(
        "SELECT * FROM forecast_runs WHERE scope=? ORDER BY created_at DESC LIMIT 1",
        (f"ae:{owner_id}",))).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["narrative"] = json.loads(d.pop("narrative_json") or "{}")
    except json.JSONDecodeError:
        d["narrative"] = {}
    d.pop("input_snapshot", None)
    return d


async def forecast_targets(conn: aiosqlite.Connection) -> List[Dict[str, Any]]:
    """List of AE owners that currently have open S1-5 pipeline (for the picker)."""
    roles = await load_role_sets(conn)
    rows = await (await conn.execute(
        """SELECT owner_id, COUNT(*) n, SUM(COALESCE(amount,0)) amt FROM deals
           WHERE is_open=1 AND current_stage_order BETWEEN 1 AND 5
           GROUP BY owner_id ORDER BY amt DESC""")).fetchall()
    # Only roster members (AEs own Stage 1+ pipeline); ignore former/other reps.
    return [{"owner_id": r["owner_id"], "name": owner_display(r["owner_id"], roles),
             "open_deals": r["n"], "open_amount": r["amt"], "in_roster": True}
            for r in rows if r["owner_id"] in roles.roster]
