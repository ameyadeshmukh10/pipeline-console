"""Weekly metric-history snapshots.

Gap 1 — S1->S3 velocity periods (median/mean/n for full quarter + thirds).
Gap 2 — funnel gate conversion (rate + advanced/lost/denom per gate).

It REUSES the Execution report's exact functions (``velocity_s1_s3_periods`` and
``funnel_summary``) so the stored history always equals what the UI shows — zero
math duplication. Rows are written to a SEPARATE append-only ``history.db``
(config.HISTORY_DB_PATH) so the precious time-series is isolated from the
rebuilt-every-sync mirror and safe to keep on a persistent volume.

Each snapshot closes out the most recently completed ISO week (Mon-Sun, business
tz — identical to how every weekly report buckets), labelled by that ISO week.
"""
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from . import db
from .config import settings
from .metrics.common import load_deal_views
from .metrics.execution import funnel_summary, velocity_s1_s3_periods
from .metrics.windows import (iso_week_key, now_utc, week_start_date,
                              window_end_dt, window_start_dt)
from .sync.runner import run_sync
from .sync.status import sync_manager

VELOCITY_PERIODS = ["full", "first_third", "second_third", "last_third"]

HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS velocity_history (
    quarter_start TEXT NOT NULL,
    iso_year      INTEGER NOT NULL,
    iso_week      INTEGER NOT NULL,
    period        TEXT NOT NULL,   -- full | first_third | second_third | last_third
    median        REAL,
    mean          REAL,
    n             INTEGER,
    taken_at      TEXT,
    PRIMARY KEY (quarter_start, iso_year, iso_week, period)
);
CREATE TABLE IF NOT EXISTS funnel_history (
    quarter_start TEXT NOT NULL,
    iso_year      INTEGER NOT NULL,
    iso_week      INTEGER NOT NULL,
    gate          TEXT NOT NULL,   -- S0_S1 .. S5_WON
    rate          REAL,
    advanced      INTEGER,
    lost          INTEGER,
    denom         INTEGER,
    taken_at      TEXT,
    PRIMARY KEY (quarter_start, iso_year, iso_week, gate)
);
CREATE TABLE IF NOT EXISTS snapshot_runs (
    id            TEXT PRIMARY KEY,
    taken_at      TEXT,
    iso_year      INTEGER,
    iso_week      INTEGER,
    quarter_start TEXT,
    status        TEXT,
    reason        TEXT,
    note          TEXT
);
"""


async def history_conn() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(settings.HISTORY_DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA busy_timeout=5000;")
    return conn


async def init_history_db() -> None:
    Path(settings.HISTORY_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = await history_conn()
    try:
        await conn.executescript(HISTORY_SCHEMA)
        await conn.commit()
    finally:
        await conn.close()


def last_complete_week(now: datetime) -> Tuple[int, int]:
    """The ISO (year, week) that has just fully finished (Mon-Sun, business tz)."""
    cy, cw = iso_week_key(now)
    prev = week_start_date(cy, cw) - timedelta(seconds=1)  # Sun 23:59:59 of the prior week
    return iso_week_key(prev)


def target_moment(now: datetime, weekday: int, hour: int) -> datetime:
    """The configured run moment (weekday+hour, business tz) of the CURRENT ISO week."""
    cy, cw = iso_week_key(now)
    return week_start_date(cy, cw) + timedelta(days=int(weekday), hours=int(hour))


def build_rows(vel: Dict[str, Any], fun: Dict[str, Any], quarter_start: str,
               iy: int, iw: int, taken_at: str) -> Tuple[List[tuple], List[tuple]]:
    """Pure transform: metric dicts -> normalized row tuples (unit-tested)."""
    vrows = [(quarter_start, iy, iw, p, vel[p]["median"], vel[p]["mean"], vel[p]["n"], taken_at)
             for p in VELOCITY_PERIODS if p in vel]
    frows = [(quarter_start, iy, iw, g["key"], g["rate"], g["advanced"], g["lost"], g["denom"], taken_at)
             for g in fun.get("gates", [])]
    return vrows, frows


async def has_snapshot(conn: aiosqlite.Connection, quarter_start: str, iy: int, iw: int) -> bool:
    row = await (await conn.execute(
        "SELECT 1 FROM snapshot_runs WHERE quarter_start=? AND iso_year=? AND iso_week=? AND status='success' LIMIT 1",
        (quarter_start, iy, iw))).fetchone()
    return row is not None


async def write_snapshot(sync_first: bool = True, week: Optional[Tuple[int, int]] = None,
                         reason: str = "manual") -> Dict[str, Any]:
    """Compute the two Execution aggregates and persist them for one ISO week."""
    if sync_first and not sync_manager.state.running:
        try:
            await run_sync()  # refresh the mirror so the numbers are current
        except Exception:
            pass  # snapshot whatever's in the mirror rather than fail the week

    mconn = await db.get_conn()
    try:
        deals = list((await load_deal_views(mconn, roster_only=False)).values())
    finally:
        await mconn.close()

    qstart, qend = window_start_dt(), window_end_dt()
    vel = velocity_s1_s3_periods(deals, qstart, qend)   # reused from Execution
    fun = funnel_summary(deals, qstart, qend)           # reused from Execution

    now = now_utc()
    iy, iw = week or last_complete_week(now)
    # History rows stay labelled by the window start (column name kept for
    # continuity with pre-window "quarter_start" rows).
    quarter_start = qstart.date().isoformat()
    taken_at = now.isoformat()
    vrows, frows = build_rows(vel, fun, quarter_start, iy, iw, taken_at)

    hconn = await history_conn()
    try:
        await hconn.executemany(
            "INSERT OR REPLACE INTO velocity_history(quarter_start,iso_year,iso_week,period,median,mean,n,taken_at) VALUES(?,?,?,?,?,?,?,?)",
            vrows)
        await hconn.executemany(
            "INSERT OR REPLACE INTO funnel_history(quarter_start,iso_year,iso_week,gate,rate,advanced,lost,denom,taken_at) VALUES(?,?,?,?,?,?,?,?,?)",
            frows)
        await hconn.execute(
            "INSERT INTO snapshot_runs(id,taken_at,iso_year,iso_week,quarter_start,status,reason,note) VALUES(?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, taken_at, iy, iw, quarter_start, "success", reason,
             f"{len(vrows)} velocity + {len(frows)} funnel rows"))
        await hconn.commit()
    finally:
        await hconn.close()

    return {"iso_year": iy, "iso_week": iw, "quarter_start": quarter_start,
            "velocity_rows": len(vrows), "funnel_rows": len(frows), "taken_at": taken_at}


async def maybe_run_due() -> Optional[Dict[str, Any]]:
    """Scheduler entrypoint: snapshot the last completed ISO week if it's due and
    not already recorded. Idempotent (one row per week) + self-healing (catches up
    if a restart made it miss the exact minute)."""
    conn = await db.get_conn()
    try:
        s = await db.get_all_settings(conn)
    finally:
        await conn.close()
    if not s.get("snapshot_enabled", True):
        return None

    now = now_utc()
    if now < target_moment(now, s.get("snapshot_weekday", 0), s.get("snapshot_hour", 6)):
        return None

    iy, iw = last_complete_week(now)
    quarter_start = window_start_dt().date().isoformat()
    hconn = await history_conn()
    try:
        if await has_snapshot(hconn, quarter_start, iy, iw):
            return None
    finally:
        await hconn.close()

    return await write_snapshot(sync_first=True, week=(iy, iw), reason="scheduled")


async def read_history() -> Dict[str, Any]:
    conn = await history_conn()
    try:
        vel = db.rows_to_dicts(await (await conn.execute(
            "SELECT * FROM velocity_history ORDER BY quarter_start, iso_year, iso_week, period")).fetchall())
        fun = db.rows_to_dicts(await (await conn.execute(
            "SELECT * FROM funnel_history ORDER BY quarter_start, iso_year, iso_week, gate")).fetchall())
        runs = db.rows_to_dicts(await (await conn.execute(
            "SELECT * FROM snapshot_runs ORDER BY taken_at DESC LIMIT 100")).fetchall())
        return {"velocity": vel, "funnel": fun, "runs": runs}
    finally:
        await conn.close()
