"""Pure derivation of a deal's stage timeline from the hs_v2 stage-date props.

No HTTP, no DB — just dicts in, dicts out, so it is trivially unit-testable.
Handles stage SKIPS (a skipped stage has no entered timestamp), BACK-MOVEMENT
(ordering is by time, not stage order), and uses latest-entry-wins because the
v2 props store only the latest entry/exit per stage.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..constants import STAGES, entered_prop, exited_prop
from ..metrics.windows import iso_week_key, now_utc, parse_ts, to_iso_utc


def derive_stage_history(
    props: Dict[str, Any],
    current_stage_id: Optional[str],
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (events, transitions) for one deal.

    events: one row per stage the deal entered, time-ordered.
    transitions: one row per adjacent pair of entered stages.
    """
    now = now or now_utc()

    candidates: List[Dict[str, Any]] = []
    for s in STAGES:
        sid = s["stage_id"]
        entered = parse_ts(props.get(entered_prop(sid)))
        if entered is None:
            continue  # stage never entered -> skip (this is how skips manifest)
        exited = parse_ts(props.get(exited_prop(sid)))
        candidates.append({
            "stage_id": sid,
            "stage_order": s["order"],
            "entered": entered,
            "exited": exited,
        })

    candidates.sort(key=lambda c: c["entered"])

    events: List[Dict[str, Any]] = []
    for c in candidates:
        entered, exited = c["entered"], c["exited"]
        iy, iw = iso_week_key(entered)
        dwell = (exited - entered) if exited is not None else (now - entered)
        events.append({
            "stage_id": c["stage_id"],
            "stage_order": c["stage_order"],
            "entered_at": to_iso_utc(entered),
            "exited_at": to_iso_utc(exited),
            "entered_iso_year": iy,
            "entered_iso_week": iw,
            "dwell_seconds": int(dwell.total_seconds()),
            "is_current": 1 if c["stage_id"] == current_stage_id else 0,
        })

    transitions: List[Dict[str, Any]] = []
    for a, b in zip(candidates, candidates[1:]):
        ty, tw = iso_week_key(b["entered"])
        gap = (b["entered"] - a["entered"]).total_seconds()
        transitions.append({
            "from_stage_order": a["stage_order"],
            "to_stage_order": b["stage_order"],
            "transition_at": to_iso_utc(b["entered"]),
            "transition_iso_year": ty,
            "transition_iso_week": tw,
            "gap_seconds": int(gap),
            "is_skip": 1 if (b["stage_order"] - a["stage_order"]) > 1 else 0,
        })

    return events, transitions
