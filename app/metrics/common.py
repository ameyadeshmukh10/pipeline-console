"""Shared loaders + stage-order helpers for the reports. Loads every deal with
its stage-entry timestamps into memory (small dataset) so cohort/conversion/
velocity math is plain Python with stage-order closure.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from ..constants import AE_IDS, ROSTER_BY_ID, ROSTER_IDS, S0_ALLOWED_IDS, SDR_IDS
from .windows import parse_ts, week_end_date, week_start_date

OTHER = "other"          # bucket key for off-roster / unexpected owners
UNKNOWN = "unknown"      # bucket key for a missing creator/owner id
WON_ORDER = 6
LOST_ORDER = 7
Week = Tuple[int, int]


@dataclass
class DealView:
    deal_id: str
    owner_id: Optional[str]
    created_by: Optional[str]
    current_order: Optional[int]
    is_open: int
    amount: Optional[float]
    hs_prob: Optional[float]
    createdate: Optional[datetime]
    closedate: Optional[datetime]
    create_iso_year: Optional[int]
    create_iso_week: Optional[int]
    dealstage: Optional[str]
    dealname: Optional[str]
    entries: Dict[int, datetime] = field(default_factory=dict)  # stage_order -> entered_at

    # --- stage-order closure helpers (reaching S_k implies S_<k passed) ----
    def entered_at(self, order: int) -> Optional[datetime]:
        return self.entries.get(order)

    @property
    def s0_at(self) -> Optional[datetime]:
        return self.entries.get(0)

    @property
    def lost_at(self) -> Optional[datetime]:
        return self.entries.get(LOST_ORDER)

    @property
    def won_at(self) -> Optional[datetime]:
        return self.entries.get(WON_ORDER)

    def first_reach(self, min_order: int) -> Optional[datetime]:
        """Earliest entry into any pipeline/won stage at or beyond min_order
        (excludes the lost stage). This is the closure-aware 'reached S_k'."""
        times = [t for o, t in self.entries.items() if min_order <= o <= WON_ORDER]
        return min(times) if times else None

    def ever_reached(self, order: int) -> bool:
        return self.first_reach(order) is not None

    @property
    def max_pipeline_order(self) -> int:
        """Furthest pipeline/won stage reached (0..6); 0 if only seen at S0."""
        orders = [o for o in self.entries if 0 <= o <= WON_ORDER]
        return max(orders) if orders else 0


async def load_deal_views(conn: aiosqlite.Connection, roster_only: bool = True,
                          by: str = "owner") -> Dict[str, DealView]:
    """Load deals as DealViews. By default IGNORES deals not owned by the
    current roster. ``by='creator'`` scopes/filters on the deal's CREATOR
    (hs_created_by_user_id) instead — used by the SDR-creation report."""
    views: Dict[str, DealView] = {}
    deals = await (await conn.execute(
        """SELECT deal_id, owner_id, created_by, current_stage_order, is_open, amount,
                  hs_deal_stage_probability, createdate, closedate, create_iso_year,
                  create_iso_week, dealstage, dealname FROM deals""")).fetchall()
    for d in deals:
        if roster_only:
            key = d["created_by"] if by == "creator" else d["owner_id"]
            if key not in ROSTER_IDS:
                continue
        views[d["deal_id"]] = DealView(
            deal_id=d["deal_id"], owner_id=d["owner_id"], created_by=d["created_by"],
            current_order=d["current_stage_order"], is_open=d["is_open"],
            amount=d["amount"], hs_prob=d["hs_deal_stage_probability"],
            createdate=parse_ts(d["createdate"]),
            closedate=parse_ts(d["closedate"]),
            create_iso_year=d["create_iso_year"], create_iso_week=d["create_iso_week"],
            dealstage=d["dealstage"], dealname=d["dealname"],
        )
    for e in await (await conn.execute(
            "SELECT deal_id, stage_order, entered_at FROM deal_stage_events")).fetchall():
        v = views.get(e["deal_id"])
        if v is None:
            continue
        dt = parse_ts(e["entered_at"])
        if dt is not None:
            v.entries[e["stage_order"]] = dt
    return views


def sdr_key(owner_id: Optional[str], sdr_ids=SDR_IDS) -> str:
    return owner_id if owner_id in sdr_ids else OTHER


def ae_key(owner_id: Optional[str], ae_ids=AE_IDS) -> str:
    return owner_id if owner_id in ae_ids else OTHER


@dataclass
class RoleSets:
    sdr: set
    ae: set
    se: set
    never_owns: set
    roster: set
    s0_allowed: set      # SDRs + full-lifecycle AEs may own a Stage 0
    names: Dict[str, str]


async def load_role_sets(conn: aiosqlite.Connection) -> RoleSets:
    rows = await (await conn.execute("SELECT * FROM owner_roles")).fetchall()
    sdr, ae, se, never, roster, s0 = set(), set(), set(), set(), set(), set()
    names: Dict[str, str] = {}
    for r in rows:
        oid = r["owner_id"]
        roster.add(oid)
        names[oid] = r["display_name"]
        if r["is_sdr"]:
            sdr.add(oid)
        if r["is_ae"]:
            ae.add(oid)
        if r["is_se"]:
            se.add(oid)
        if r["never_owns"]:
            never.add(oid)
        if r["is_sdr"] or r["full_lifecycle"]:
            s0.add(oid)
    return RoleSets(sdr=sdr, ae=ae, se=se, never_owns=never, roster=roster,
                    s0_allowed=s0, names=names)


def owner_display(owner_id: Optional[str], roles: "RoleSets") -> str:
    if not owner_id:
        return "Unassigned"
    return roles.names.get(owner_id) or f"Other ({owner_id})"


# --------------------------------------------------------------------------- #
# Shared people + group + pooling helpers (used by Pre-Pipeline AND Execution).
# Person id = creator id (Pre-Pipeline) or owner id (Execution). `total_created`
# is the in-scope count (deals created / owned) — kept under that key so the
# existing frontend bindings don't change.
# --------------------------------------------------------------------------- #
def partial_week(wk: Week, qstart: datetime, now: datetime) -> bool:
    return week_start_date(*wk) < qstart or week_end_date(*wk) > now


def sum_lists(arrs, n: int) -> List[int]:
    out = [0] * n
    for arr in arrs:
        if not arr:
            continue
        for i in range(min(n, len(arr))):
            out[i] += arr[i] or 0
    return out


def pool_components(by, member_ids, weeks, fields):
    """Sum the per-person weekly component dicts for a group's members."""
    out = {wk: {f: 0 for f in fields} for wk in weeks}
    for pid in member_ids:
        pdict = by.get(pid)
        if not pdict:
            continue
        for wk, comp in pdict.items():
            if wk not in out:
                continue
            for f in fields:
                out[wk][f] += comp.get(f, 0)
    return out


def pool_value_lists(by, member_ids, weeks):
    """Concatenate the per-person weekly value-lists for a group's members
    (used by median velocity, where values can't be summed)."""
    out = {wk: [] for wk in weeks}
    for pid in member_ids:
        pdict = by.get(pid)
        if not pdict:
            continue
        for wk, vals in pdict.items():
            if wk in out:
                out[wk].extend(vals)
    return out


async def load_person_names(conn: aiosqlite.Connection) -> Dict[str, Dict[str, Any]]:
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


def person_meta(pid: str, total: int, names: Dict[str, Dict[str, Any]],
                roles: "RoleSets") -> Dict[str, Any]:
    if pid == UNKNOWN:
        return {"id": None, "first_name": "Unknown", "name": "Unknown", "last": "",
                "in_roster": False, "role": "", "archived": False, "total_created": total}
    nm = names.get(pid)
    if nm and nm["full"]:
        first, last, full, archived = nm["first"] or nm["full"], nm["last"], nm["full"], nm["archived"]
    elif pid in roles.names:
        full = roles.names[pid]
        first, last, archived = full.split()[0], "", False
    elif pid in ROSTER_BY_ID:
        full = ROSTER_BY_ID[pid]["display_name"]
        first, last, archived = full.split()[0], "", False
    else:
        first = last = full = f"Other ({pid})"
        archived = False
    role = ("SDR" if pid in roles.sdr else "AE" if pid in roles.ae
            else "SE" if pid in roles.se else "never" if pid in roles.never_owns else "")
    return {"id": pid, "first_name": first, "name": full, "last": last,
            "in_roster": pid in roles.roster, "role": role,
            "archived": archived, "total_created": total}


def disambiguate_first_names(people: List[Dict[str, Any]]) -> None:
    counts = Counter(p["first_name"] for p in people)
    for p in people:
        if counts[p["first_name"]] > 1 and p.get("last"):
            p["first_name"] = f"{p['first_name']} {p['last'][0]}."


def normalize_groups(raw, totals, people):
    """Validate a settings groups list + resolve member names/totals."""
    first_by_id = {p["id"]: p["first_name"] for p in people if p["id"]}
    full_by_id = {p["id"]: p["name"] for p in people if p["id"]}
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
