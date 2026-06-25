"""HTTP API router. Routes are thin: they read the SQLite mirror / cached
metrics and return JSON. Heavy work happens in sync/ and metrics/.
"""
import asyncio
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from . import db
from .config import settings
from .constants import STAGES
from .models import ForecastRunRequest, SettingsUpdate
from .sync.runner import run_sync
from .sync.status import sync_manager

router = APIRouter()


@router.get("/health")
async def health() -> Dict[str, Any]:
    """Liveness probe for Railway (no DB access — fast, always 200 once booted)."""
    return {"status": "ok"}


@router.post("/sync")
async def trigger_sync() -> Dict[str, Any]:
    """Kick off a manual sync in the background. 409 if one is already running."""
    if not settings.hubspot_ready:
        raise HTTPException(503, "HUBSPOT_ACCESS_TOKEN is not configured in .env")
    if sync_manager.state.running:
        raise HTTPException(409, "A sync is already in progress")
    # Reserve synchronously (no await between check and set -> atomic in asyncio).
    sync_manager.state.running = True

    async def _bg() -> None:
        try:
            await run_sync()
        except Exception:  # error is recorded on the status object
            sync_manager.state.running = False

    asyncio.create_task(_bg())
    return {"started": True, **sync_manager.snapshot()}


@router.get("/meta")
async def get_meta() -> Dict[str, Any]:
    """Static reference for the UI: stages, roster, last sync, deal counts."""
    conn = await db.get_conn()
    try:
        roles = db.rows_to_dicts(await (await conn.execute(
            "SELECT * FROM owner_roles ORDER BY display_name")).fetchall())
        owners = db.rows_to_dicts(await (await conn.execute(
            "SELECT owner_id, first_name, last_name, email, in_roster FROM owners")).fetchall())
        counts = db.rows_to_dicts(await (await conn.execute(
            """SELECT dealstage, COUNT(*) AS n FROM deals WHERE is_open=1 GROUP BY dealstage""")).fetchall())
        last = await (await conn.execute(
            "SELECT MAX(finished_at) AS t FROM sync_runs WHERE status='success'")).fetchone()
        total_deals = await (await conn.execute("SELECT COUNT(*) AS n FROM deals")).fetchone()
        win_window = await db.get_setting(conn, "quarter_start", settings.QUARTER_START)
        return {
            "stages": STAGES,
            "roster": roles,
            "owners": owners,
            "open_counts": {c["dealstage"]: c["n"] for c in counts},
            "total_deals": total_deals["n"] if total_deals else 0,
            "last_success_at": last["t"] if last else None,
            "quarter_start": win_window,
            "pipeline_id": settings.PIPELINE_ID,
            "hubspot_ready": settings.hubspot_ready,
            "anthropic_ready": settings.anthropic_ready,
        }
    finally:
        await conn.close()


@router.get("/settings")
async def get_settings() -> Dict[str, Any]:
    conn = await db.get_conn()
    try:
        values = await db.get_all_settings(conn)
        roles = db.rows_to_dicts(await (await conn.execute(
            "SELECT * FROM owner_roles ORDER BY display_name")).fetchall())
        # Surface off-roster owners (seen on deals but not in roster) for assignment.
        off = db.rows_to_dicts(await (await conn.execute(
            """SELECT d.owner_id, COUNT(*) AS n FROM deals d
               LEFT JOIN owner_roles r ON r.owner_id = d.owner_id
               WHERE r.owner_id IS NULL AND d.owner_id IS NOT NULL AND d.owner_id <> ''
               GROUP BY d.owner_id ORDER BY n DESC""")).fetchall())
        from .metrics.prepipeline import list_creators
        from .metrics.execution import list_owners
        creators = await list_creators(conn)
        owners = await list_owners(conn)
        return {"values": values, "roster": roles, "off_roster_owners": off,
                "creators": creators, "owners": owners}
    finally:
        await conn.close()


@router.put("/settings")
async def update_settings(body: SettingsUpdate) -> Dict[str, Any]:
    conn = await db.get_conn()
    try:
        for key, value in body.values.items():
            await db.set_setting(conn, key, value)
        await conn.commit()
        values = await db.get_all_settings(conn)
        return {"ok": True, "values": values}
    finally:
        await conn.close()


@router.get("/sync/status")
async def get_sync_status() -> Dict[str, Any]:
    return sync_manager.snapshot()


@router.get("/prepipeline")
async def get_prepipeline() -> Dict[str, Any]:
    from .metrics.prepipeline import pre_pipeline_report
    conn = await db.get_conn()
    try:
        return await pre_pipeline_report(conn)
    finally:
        await conn.close()


@router.get("/execution")
async def get_execution() -> Dict[str, Any]:
    from .metrics.execution import execution_report
    conn = await db.get_conn()
    try:
        return await execution_report(conn)
    finally:
        await conn.close()


@router.get("/flags")
async def get_flags() -> Dict[str, Any]:
    from .metrics.flags import compute_all_flags
    conn = await db.get_conn()
    try:
        return await compute_all_flags(conn)
    finally:
        await conn.close()


@router.get("/deals")
async def get_deals(owner_id: Optional[str] = None, stage: Optional[str] = None) -> Dict[str, Any]:
    from .metrics.accountability import deals_table
    conn = await db.get_conn()
    try:
        return await deals_table(conn, owner_id=owner_id, stage=stage)
    finally:
        await conn.close()


@router.get("/deals/{deal_id}")
async def get_deal_inspector(deal_id: str) -> Dict[str, Any]:
    from .metrics.accountability import deal_inspector
    conn = await db.get_conn()
    try:
        result = await deal_inspector(conn, deal_id)
        if result is None:
            raise HTTPException(404, f"Deal {deal_id} not found")
        return result
    finally:
        await conn.close()


@router.get("/scorecard")
async def get_scorecard() -> Dict[str, Any]:
    from .metrics.accountability import rep_scorecard
    conn = await db.get_conn()
    try:
        return await rep_scorecard(conn)
    finally:
        await conn.close()


@router.get("/cohorts")
async def get_cohorts() -> Dict[str, Any]:
    from .metrics.cohorts import cohort_report
    conn = await db.get_conn()
    try:
        return await cohort_report(conn)
    finally:
        await conn.close()


@router.get("/forecast/targets")
async def get_forecast_targets() -> Dict[str, Any]:
    from .metrics.forecast import forecast_targets
    conn = await db.get_conn()
    try:
        return {"targets": await forecast_targets(conn)}
    finally:
        await conn.close()


@router.get("/forecast")
async def get_forecast(owner_id: str, target: Optional[float] = None) -> Dict[str, Any]:
    """Deterministic $ packet + the last stored agent narrative (no LLM call)."""
    from .metrics.forecast import build_forecast_packet, latest_forecast_run
    conn = await db.get_conn()
    try:
        packet = await build_forecast_packet(conn, owner_id, target)
        last = await latest_forecast_run(conn, owner_id)
        return {"packet": packet, "last_run": last}
    finally:
        await conn.close()


@router.post("/forecast/run")
async def run_forecast(body: ForecastRunRequest) -> Dict[str, Any]:
    """Run the LLM forecast agent for one AE (synchronous; returns the result)."""
    if not settings.anthropic_ready:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured in .env")
    from .agents.forecast_agent import run_forecast_agent
    from .metrics.forecast import build_forecast_packet, store_forecast_run
    conn = await db.get_conn()
    try:
        packet = await build_forecast_packet(conn, body.owner_id, body.target)
        narrative = await run_forecast_agent(packet)
        run_id = await store_forecast_run(conn, packet, narrative)
        return {"run_id": run_id, "packet": packet, "narrative": narrative}
    finally:
        await conn.close()
