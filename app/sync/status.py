"""In-memory sync status singleton + lock. One local user, one sync at a time."""
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional

# Coarse progress weighting per phase so the UI bar moves meaningfully.
_PHASE_BASE = {
    "idle": 0,
    "starting": 2,
    "fetching_owners": 5,
    "fetching_deals": 10,      # scales 10 -> 45 by deals_seen/deals_total
    "deriving_events": 50,
    "fetching_activity": 60,   # scales 60 -> 85 by open_deals progress
    "computing": 90,
    "done": 100,
    "error": 100,
}
_PHASE_LABEL = {
    "idle": "Idle",
    "starting": "Starting…",
    "fetching_owners": "Fetching owners…",
    "fetching_deals": "Fetching deals…",
    "deriving_events": "Deriving stage timelines…",
    "fetching_activity": "Checking activity on open deals…",
    "computing": "Computing reports…",
    "done": "Done",
    "error": "Error",
}


@dataclass
class SyncState:
    running: bool = False
    phase: str = "idle"
    deals_seen: int = 0
    deals_total: int = 0
    open_deals_total: int = 0
    open_deals_scanned: int = 0
    engagements_upserted: int = 0
    request_count: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    last_success_at: Optional[str] = None
    error: Optional[str] = None
    sync_id: Optional[str] = None

    def pct(self) -> int:
        base = _PHASE_BASE.get(self.phase, 0)
        if self.phase == "fetching_deals" and self.deals_total:
            frac = min(1.0, self.deals_seen / self.deals_total)
            return int(10 + frac * 35)
        if self.phase == "fetching_activity" and self.open_deals_total:
            frac = min(1.0, self.open_deals_scanned / self.open_deals_total)
            return int(60 + frac * 25)
        return base

    def snapshot(self) -> Dict:
        return {
            "running": self.running,
            "phase": self.phase,
            "phase_label": _PHASE_LABEL.get(self.phase, self.phase),
            "pct": self.pct(),
            "deals_seen": self.deals_seen,
            "deals_total": self.deals_total,
            "open_deals_total": self.open_deals_total,
            "open_deals_scanned": self.open_deals_scanned,
            "engagements_upserted": self.engagements_upserted,
            "request_count": self.request_count,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_success_at": self.last_success_at,
            "error": self.error,
            "sync_id": self.sync_id,
        }


class SyncManager:
    def __init__(self) -> None:
        self.state = SyncState()
        self.lock = asyncio.Lock()

    def snapshot(self) -> Dict:
        return self.state.snapshot()


sync_manager = SyncManager()
