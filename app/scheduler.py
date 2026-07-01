"""In-process weekly scheduler for metric-history snapshots.

Runs inside the web service (the only process that can hold the Railway volume,
since a volume attaches to exactly one service). Ticks hourly and, via
history.maybe_run_due(), writes at most one snapshot per completed ISO week at or
after the configured day/time. Self-healing across restarts; never crashes the
app (all exceptions swallowed).
"""
import asyncio

from .history import maybe_run_due

TICK_SECONDS = 3600  # hourly is plenty for a weekly job


async def snapshot_scheduler() -> None:
    await asyncio.sleep(30)  # let startup + any first sync settle
    while True:
        try:
            await maybe_run_due()
        except Exception:  # pragma: no cover - a bad tick must not kill the loop
            pass
        await asyncio.sleep(TICK_SECONDS)
