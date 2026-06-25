"""Shared loaders + stage-order helpers for the reports. Loads every deal with
its stage-entry timestamps into memory (small dataset) so cohort/conversion/
velocity math is plain Python with stage-order closure.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import aiosqlite

from ..constants import AE_IDS, ROSTER_IDS, S0_ALLOWED_IDS, SDR_IDS
from .windows import parse_ts

OTHER = "other"          # bucket key for off-roster / unexpected owners
WON_ORDER = 6
LOST_ORDER = 7


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
