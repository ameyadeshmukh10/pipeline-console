"""Sync orchestration: HubSpot -> SQLite mirror -> derived events -> rollups.

Phase-transactional and idempotent: deals/engagements upsert by id and the
stage event/transition tables are rebuilt per deal, so re-running "Sync now"
is always safe and self-healing.
"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from .. import db
from ..config import settings
from ..constants import ROSTER_IDS, STAGE_ORDER_BY_ID
from ..hubspot.client import HubSpotClient
from ..hubspot.deals import fetch_deals, fetch_owners
from ..hubspot.engagements import fetch_engagements_for_deals
from ..metrics.windows import iso_week_key, now_utc, parse_ts
from .events import derive_stage_history
from .rollups import recompute_rollups
from .status import sync_manager


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _b(v: Any) -> int:
    return 1 if str(v).lower() in ("true", "1") else 0


async def _upsert_owners(conn: aiosqlite.Connection, owners: List[Dict[str, Any]]) -> None:
    now = now_utc().isoformat()
    for o in owners:
        oid = str(o.get("id"))
        await conn.execute(
            """INSERT INTO owners(owner_id, first_name, last_name, email, archived, in_roster, last_synced_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(owner_id) DO UPDATE SET
                 first_name=excluded.first_name, last_name=excluded.last_name,
                 email=excluded.email, archived=excluded.archived,
                 in_roster=excluded.in_roster, last_synced_at=excluded.last_synced_at""",
            (oid, o.get("firstName"), o.get("lastName"), o.get("email"),
             1 if o.get("archived") else 0, 1 if oid in ROSTER_IDS else 0, now),
        )


async def _upsert_deals_and_events(conn: aiosqlite.Connection, deals: List[Dict[str, Any]],
                                   now: datetime) -> int:
    now_iso = now.isoformat()
    events_total = 0
    for d in deals:
        deal_id = str(d.get("id"))
        p = d.get("properties", {}) or {}
        dealstage = p.get("dealstage")
        order = STAGE_ORDER_BY_ID.get(dealstage)
        is_open = 0 if _b(p.get("hs_is_closed")) else 1
        ciy, ciw = iso_week_key(parse_ts(p.get("createdate")))

        await conn.execute(
            """INSERT INTO deals(
                  deal_id, dealname, pipeline, dealstage, current_stage_order, owner_id, created_by,
                  amount, closedate, createdate, create_iso_year, create_iso_week,
                  hs_lastmodifieddate, notes_last_updated, hs_deal_stage_probability,
                  days_to_close, hs_is_closed, hs_is_closed_won, is_open, raw_props,
                  first_seen_at, last_synced_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(deal_id) DO UPDATE SET
                  dealname=excluded.dealname, pipeline=excluded.pipeline,
                  dealstage=excluded.dealstage, current_stage_order=excluded.current_stage_order,
                  owner_id=excluded.owner_id, created_by=excluded.created_by,
                  amount=excluded.amount, closedate=excluded.closedate,
                  createdate=excluded.createdate, create_iso_year=excluded.create_iso_year,
                  create_iso_week=excluded.create_iso_week, hs_lastmodifieddate=excluded.hs_lastmodifieddate,
                  notes_last_updated=excluded.notes_last_updated,
                  hs_deal_stage_probability=excluded.hs_deal_stage_probability,
                  days_to_close=excluded.days_to_close, hs_is_closed=excluded.hs_is_closed,
                  hs_is_closed_won=excluded.hs_is_closed_won, is_open=excluded.is_open,
                  raw_props=excluded.raw_props, last_synced_at=excluded.last_synced_at""",
            (deal_id, p.get("dealname"), p.get("pipeline"), dealstage, order,
             p.get("hubspot_owner_id") or None, p.get("hs_created_by_user_id") or None,
             _f(p.get("amount")), p.get("closedate"),
             p.get("createdate"), ciy, ciw, p.get("hs_lastmodifieddate"),
             p.get("notes_last_updated"), _f(p.get("hs_deal_stage_probability")),
             _f(p.get("days_to_close")), _b(p.get("hs_is_closed")), _b(p.get("hs_is_closed_won")),
             is_open, json.dumps(p), now_iso, now_iso),
        )

        events, transitions = derive_stage_history(p, dealstage, now=now)
        await conn.execute("DELETE FROM deal_stage_events WHERE deal_id=?", (deal_id,))
        await conn.execute("DELETE FROM deal_stage_transitions WHERE deal_id=?", (deal_id,))
        if events:
            await conn.executemany(
                """INSERT INTO deal_stage_events(
                      deal_id, stage_id, stage_order, entered_at, exited_at,
                      entered_iso_year, entered_iso_week, dwell_seconds, is_current)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                [(deal_id, e["stage_id"], e["stage_order"], e["entered_at"], e["exited_at"],
                  e["entered_iso_year"], e["entered_iso_week"], e["dwell_seconds"], e["is_current"])
                 for e in events],
            )
            events_total += len(events)
        if transitions:
            await conn.executemany(
                """INSERT INTO deal_stage_transitions(
                      deal_id, from_stage_order, to_stage_order, transition_at,
                      transition_iso_year, transition_iso_week, gap_seconds, is_skip)
                   VALUES(?,?,?,?,?,?,?,?)""",
                [(deal_id, t["from_stage_order"], t["to_stage_order"], t["transition_at"],
                  t["transition_iso_year"], t["transition_iso_week"], t["gap_seconds"], t["is_skip"])
                 for t in transitions],
            )
    return events_total


async def _engagement_target_ids(conn: aiosqlite.Connection, window_days: int,
                                 now: datetime) -> List[str]:
    """Open deals (all stages) + deals closed within the engagement window."""
    cutoff = (now - timedelta(days=int(window_days))).isoformat()
    rows = await (await conn.execute(
        """SELECT deal_id FROM deals
           WHERE is_open=1 OR (hs_is_closed=1 AND closedate IS NOT NULL AND closedate >= ?)""",
        (cutoff,))).fetchall()
    return [r["deal_id"] for r in rows]


async def _upsert_engagements(conn: aiosqlite.Connection, rows: List[Dict[str, Any]]) -> None:
    await conn.execute("DELETE FROM engagements")
    if not rows:
        return
    await conn.executemany(
        """INSERT INTO engagements(raw_id, type, deal_id, ts, last_modified, owner_id,
                                   direction, status, subject, is_automated)
           VALUES(?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(raw_id, deal_id) DO UPDATE SET
             ts=excluded.ts, last_modified=excluded.last_modified, owner_id=excluded.owner_id,
             direction=excluded.direction, status=excluded.status, subject=excluded.subject,
             is_automated=excluded.is_automated""",
        [(r["raw_id"], r["type"], r["deal_id"], r["ts"], r["last_modified"], r["owner_id"],
          r["direction"], r["status"], r["subject"], r["is_automated"]) for r in rows],
    )


async def run_sync() -> Dict[str, Any]:
    st = sync_manager.state
    st.running = True
    st.phase = "starting"
    st.error = None
    st.sync_id = uuid.uuid4().hex
    st.started_at = now_utc().isoformat()
    st.finished_at = None
    st.deals_seen = st.deals_total = 0
    st.open_deals_total = st.open_deals_scanned = 0
    st.engagements_upserted = st.request_count = 0

    t0 = time.monotonic()
    conn = await db.get_conn()
    deals: List[Dict[str, Any]] = []
    eng_rows: List[Dict[str, Any]] = []
    target_ids: List[str] = []
    events_total = 0
    try:
        await conn.execute(
            "INSERT INTO sync_runs(sync_id, started_at, status, phase) VALUES(?,?,?,?)",
            (st.sync_id, st.started_at, "running", "starting"))
        await conn.commit()

        async with HubSpotClient(concurrency=5) as client:
            st.phase = "fetching_owners"
            await _upsert_owners(conn, await fetch_owners(client))
            await conn.commit()
            st.request_count = client.request_count

            st.phase = "fetching_deals"

            def on_page(seen: int, total: int) -> None:
                st.deals_seen = seen
                st.deals_total = total
                st.request_count = client.request_count

            deals = await fetch_deals(client, settings.PIPELINE_ID, on_page=on_page)
            st.deals_total = st.deals_seen = len(deals)

            st.phase = "deriving_events"
            now = now_utc()
            events_total = await _upsert_deals_and_events(conn, deals, now)
            await conn.commit()

            window_days = await db.get_setting(conn, "closed_engagement_window_days", 90)
            target_ids = await _engagement_target_ids(conn, window_days, now)
            st.open_deals_total = len(target_ids)

            st.phase = "fetching_activity"

            def on_type(label: str, n: int) -> None:
                st.open_deals_scanned = st.open_deals_total
                st.request_count = client.request_count

            eng_rows = await fetch_engagements_for_deals(client, target_ids, on_type_done=on_type)
            await _upsert_engagements(conn, eng_rows)
            await conn.commit()
            st.engagements_upserted = len(eng_rows)
            st.request_count = client.request_count

            st.phase = "computing"
            await recompute_rollups(conn, now)
            await conn.commit()

        dur = int((time.monotonic() - t0) * 1000)
        st.finished_at = now_utc().isoformat()
        st.phase = "done"
        st.running = False
        st.last_success_at = st.finished_at
        await conn.execute(
            """UPDATE sync_runs SET finished_at=?, status='success', phase='done',
                  deals_upserted=?, events_rebuilt=?, engagements_upserted=?,
                  open_deals_scanned=?, request_count=?, duration_ms=? WHERE sync_id=?""",
            (st.finished_at, len(deals), events_total, len(eng_rows), len(target_ids),
             st.request_count, dur, st.sync_id))
        await conn.commit()
        return {"deals": len(deals), "events": events_total, "engagements": len(eng_rows),
                "open_deals": len(target_ids), "request_count": st.request_count, "duration_ms": dur}
    except Exception as e:  # noqa: BLE001
        st.phase = "error"
        st.running = False
        st.error = str(e)
        st.finished_at = now_utc().isoformat()
        try:
            await conn.execute(
                "UPDATE sync_runs SET finished_at=?, status='error', phase='error', error_message=? WHERE sync_id=?",
                (st.finished_at, str(e)[:1000], st.sync_id))
            await conn.commit()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        await conn.close()
