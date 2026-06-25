"""SQLite storage (aiosqlite). Schema DDL + connection helper + seed.

All timestamps are stored as ISO-8601 UTC strings. Derived ISO year/week
columns are stamped in the configured business timezone at sync time so the
event-dated weekly reports are plain GROUP BY queries.
"""
import json
from typing import Any, Dict, Iterable, List, Optional

import aiosqlite

from .config import settings
from .constants import DEFAULT_SETTINGS, ROSTER, STAGES, entered_prop, exited_prop

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,            -- JSON-encoded
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS stages (
    stage_id     TEXT PRIMARY KEY,
    short        TEXT,
    label        TEXT,
    stage_order  INTEGER,
    is_open      INTEGER,
    is_won       INTEGER,
    is_lost      INTEGER,
    entered_prop TEXT,
    exited_prop  TEXT
);

CREATE TABLE IF NOT EXISTS owners (
    owner_id       TEXT PRIMARY KEY,
    first_name     TEXT,
    last_name      TEXT,
    email          TEXT,
    archived       INTEGER DEFAULT 0,
    in_roster      INTEGER DEFAULT 0,
    last_synced_at TEXT
);

CREATE TABLE IF NOT EXISTS owner_roles (
    owner_id       TEXT PRIMARY KEY,
    display_name   TEXT,
    is_sdr         INTEGER DEFAULT 0,
    is_ae          INTEGER DEFAULT 0,
    is_se          INTEGER DEFAULT 0,
    never_owns     INTEGER DEFAULT 0,
    full_lifecycle INTEGER DEFAULT 0,
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS deals (
    deal_id                  TEXT PRIMARY KEY,
    dealname                 TEXT,
    pipeline                 TEXT,
    dealstage                TEXT,
    current_stage_order      INTEGER,
    owner_id                 TEXT,
    created_by               TEXT,
    amount                   REAL,
    closedate                TEXT,
    createdate               TEXT,
    create_iso_year          INTEGER,
    create_iso_week          INTEGER,
    hs_lastmodifieddate      TEXT,
    notes_last_updated       TEXT,
    hs_deal_stage_probability REAL,
    days_to_close            REAL,
    hs_is_closed             INTEGER,
    hs_is_closed_won         INTEGER,
    is_open                  INTEGER,
    raw_props                TEXT,   -- full property payload (JSON)
    first_seen_at            TEXT,
    last_synced_at           TEXT
);
CREATE INDEX IF NOT EXISTS ix_deals_owner_stage ON deals(owner_id, current_stage_order, is_open);
CREATE INDEX IF NOT EXISTS ix_deals_create_week ON deals(create_iso_year, create_iso_week);

CREATE TABLE IF NOT EXISTS deal_stage_events (
    deal_id          TEXT NOT NULL,
    stage_id         TEXT NOT NULL,
    stage_order      INTEGER,
    entered_at       TEXT,
    exited_at        TEXT,
    entered_iso_year INTEGER,
    entered_iso_week INTEGER,
    dwell_seconds    INTEGER,
    is_current       INTEGER DEFAULT 0,
    PRIMARY KEY (deal_id, stage_id)
);
CREATE INDEX IF NOT EXISTS ix_dse_week ON deal_stage_events(entered_iso_year, entered_iso_week, stage_order);
CREATE INDEX IF NOT EXISTS ix_dse_deal ON deal_stage_events(deal_id);

CREATE TABLE IF NOT EXISTS deal_stage_transitions (
    deal_id             TEXT NOT NULL,
    from_stage_order    INTEGER,
    to_stage_order      INTEGER,
    transition_at       TEXT,
    transition_iso_year INTEGER,
    transition_iso_week INTEGER,
    gap_seconds         INTEGER,
    is_skip             INTEGER DEFAULT 0,
    PRIMARY KEY (deal_id, from_stage_order, to_stage_order)
);
CREATE INDEX IF NOT EXISTS ix_dst_week ON deal_stage_transitions(transition_iso_year, transition_iso_week, to_stage_order);
CREATE INDEX IF NOT EXISTS ix_dst_deal ON deal_stage_transitions(deal_id);

CREATE TABLE IF NOT EXISTS engagements (
    raw_id        TEXT NOT NULL,
    type          TEXT NOT NULL,        -- meeting|call|email|note|task
    deal_id       TEXT NOT NULL,
    ts            TEXT,                  -- occurrence time (hs_timestamp)
    last_modified TEXT,
    owner_id      TEXT,                  -- who logged it
    direction     TEXT,                  -- in|out|NULL
    status        TEXT,                  -- outcome/disposition/status
    subject       TEXT,
    is_automated  INTEGER DEFAULT 0,
    PRIMARY KEY (raw_id, deal_id)
);
CREATE INDEX IF NOT EXISTS ix_eng_deal ON engagements(deal_id);
CREATE INDEX IF NOT EXISTS ix_eng_owner_ts ON engagements(owner_id, ts);
CREATE INDEX IF NOT EXISTS ix_eng_deal_type_ts ON engagements(deal_id, type, ts);

CREATE TABLE IF NOT EXISTS deal_activity_rollup (
    deal_id                   TEXT PRIMARY KEY,
    last_activity_ts          TEXT,
    days_since_last_activity  REAL,
    n_meetings                INTEGER DEFAULT 0,
    n_calls                   INTEGER DEFAULT 0,
    n_emails                  INTEGER DEFAULT 0,
    n_notes                   INTEGER DEFAULT 0,
    n_tasks                   INTEGER DEFAULT 0,
    n_total                   INTEGER DEFAULT 0,
    n_meetings_held           INTEGER DEFAULT 0,
    n_meetings_booked         INTEGER DEFAULT 0,
    first_meeting_ts          TEXT,
    last_meeting_ts           TEXT,
    n_since_stage_total       INTEGER DEFAULT 0,
    n_outbound                INTEGER DEFAULT 0,
    n_inbound                 INTEGER DEFAULT 0,
    has_any_meeting           INTEGER DEFAULT 0,
    has_open_task             INTEGER DEFAULT 0,
    has_next_step             INTEGER DEFAULT 0,
    pct_activity_logged_by_owner REAL,
    flags_json                TEXT,
    computed_at               TEXT
);

CREATE TABLE IF NOT EXISTS rep_week_rollup (
    owner_id                   TEXT NOT NULL,
    iso_year                   INTEGER NOT NULL,
    iso_week                   INTEGER NOT NULL,
    open_deals                 INTEGER DEFAULT 0,
    activities_logged          INTEGER DEFAULT 0,
    meetings_held              INTEGER DEFAULT 0,
    active_deals_touched       INTEGER DEFAULT 0,
    avg_activities_per_open_deal REAL,
    deals_zero_activity        INTEGER DEFAULT 0,
    deals_stalled              INTEGER DEFAULT 0,
    no_next_step               INTEGER DEFAULT 0,
    PRIMARY KEY (owner_id, iso_year, iso_week)
);

CREATE TABLE IF NOT EXISTS forecast_runs (
    run_id            TEXT PRIMARY KEY,
    scope             TEXT,
    created_at        TEXT,
    closed_won_qtd    REAL,
    commit_amount     REAL,
    most_likely_amount REAL,
    best_case_amount  REAL,
    worst_case_amount REAL,
    weighted_amount   REAL,
    open_pipeline     REAL,
    pipeline_coverage REAL,
    weighted_coverage REAL,
    target_amount     REAL,
    gap_to_target     REAL,
    model             TEXT,
    narrative_json    TEXT,
    input_snapshot    TEXT
);
CREATE INDEX IF NOT EXISTS ix_forecast_scope ON forecast_runs(scope, created_at);

CREATE TABLE IF NOT EXISTS forecast_deal_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT,
    deal_id         TEXT,
    category        TEXT,          -- discuss|anomaly|close_lost_candidate|per_deal_call
    weighted_amount REAL,
    rationale       TEXT,
    flags           TEXT
);
CREATE INDEX IF NOT EXISTS ix_fdi_run ON forecast_deal_items(run_id);

CREATE TABLE IF NOT EXISTS sync_runs (
    sync_id              TEXT PRIMARY KEY,
    started_at           TEXT,
    finished_at          TEXT,
    status               TEXT,           -- running|success|error
    phase                TEXT,
    deals_upserted       INTEGER DEFAULT 0,
    events_rebuilt       INTEGER DEFAULT 0,
    engagements_upserted INTEGER DEFAULT 0,
    open_deals_scanned   INTEGER DEFAULT 0,
    request_count        INTEGER DEFAULT 0,
    duration_ms          INTEGER,
    error_message        TEXT
);
"""


def connect() -> aiosqlite.Connection:
    """Return a NEW connection. Use as ``async with db.connect() as conn:``."""
    conn = aiosqlite.connect(settings.DB_PATH)
    return conn


async def _configure(conn: aiosqlite.Connection) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL;")
    await conn.execute("PRAGMA foreign_keys=ON;")
    await conn.execute("PRAGMA busy_timeout=5000;")


async def get_conn() -> aiosqlite.Connection:
    conn = await connect()
    await _configure(conn)
    return conn


async def init_db() -> None:
    """Create schema if missing and seed reference data (idempotent)."""
    conn = await get_conn()
    try:
        await conn.executescript(SCHEMA)
        await _migrate(conn)
        await _seed_stages(conn)
        await _seed_owner_roles(conn)
        await _seed_settings(conn)
        await conn.commit()
    finally:
        await conn.close()


async def _migrate(conn: aiosqlite.Connection) -> None:
    """Lightweight additive migrations for existing DBs."""
    for col, ddl in [("created_by", "ALTER TABLE deals ADD COLUMN created_by TEXT")]:
        try:
            await conn.execute(ddl)
        except Exception:  # column already exists
            pass


async def _seed_stages(conn: aiosqlite.Connection) -> None:
    for s in STAGES:
        await conn.execute(
            """INSERT INTO stages(stage_id, short, label, stage_order, is_open, is_won, is_lost, entered_prop, exited_prop)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(stage_id) DO UPDATE SET
                 short=excluded.short, label=excluded.label, stage_order=excluded.stage_order,
                 is_open=excluded.is_open, is_won=excluded.is_won, is_lost=excluded.is_lost,
                 entered_prop=excluded.entered_prop, exited_prop=excluded.exited_prop""",
            (s["stage_id"], s["short"], s["label"], s["order"], s["is_open"], s["is_won"],
             s["is_lost"], entered_prop(s["stage_id"]), exited_prop(s["stage_id"])),
        )


async def _seed_owner_roles(conn: aiosqlite.Connection) -> None:
    # Roster is config-driven (constants.ROSTER) and the Settings UI is read-only
    # for roles, so fully re-sync the table from config on every startup.
    await conn.execute("DELETE FROM owner_roles")
    for r in ROSTER:
        await conn.execute(
            """INSERT OR REPLACE INTO owner_roles(owner_id, display_name, is_sdr, is_ae, is_se, never_owns, full_lifecycle, notes)
               VALUES(?,?,?,?,?,?,?,?)""",
            (r["owner_id"], r["display_name"], r["is_sdr"], r["is_ae"], r["is_se"],
             r["never_owns"], r["full_lifecycle"], r["notes"]),
        )


async def _seed_settings(conn: aiosqlite.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        cur = await conn.execute("SELECT 1 FROM settings WHERE key=?", (key,))
        if await cur.fetchone():
            continue
        await conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,datetime('now'))",
            (key, json.dumps(value)),
        )


# --- settings helpers -------------------------------------------------------
async def get_setting(conn: aiosqlite.Connection, key: str, default: Any = None) -> Any:
    cur = await conn.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cur.fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return default


async def get_all_settings(conn: aiosqlite.Connection) -> Dict[str, Any]:
    cur = await conn.execute("SELECT key, value FROM settings")
    out: Dict[str, Any] = {}
    for row in await cur.fetchall():
        try:
            out[row["key"]] = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            out[row["key"]] = row["value"]
    return out


async def set_setting(conn: aiosqlite.Connection, key: str, value: Any) -> None:
    await conn.execute(
        """INSERT INTO settings(key, value, updated_at) VALUES(?,?,datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
        (key, json.dumps(value)),
    )


def rows_to_dicts(rows: Iterable[aiosqlite.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]
