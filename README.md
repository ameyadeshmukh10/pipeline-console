# Pipeline Console

Clear sales-pipeline & forecast reporting over HubSpot. A local web app that mirrors your
HubSpot **Sales Pipeline** into SQLite and turns it into rigorous, weekly, event-dated
analytics that HubSpot itself won't show you — plus an LLM forecasting agent.

Backend: **Python / FastAPI / SQLite**. Frontend: a no-build **vanilla-JS + Chart.js** SPA.
Reasoning: **Anthropic Claude** (`claude-opus-4-8`).

## What it shows

1. **Pre-Pipeline** (SDR quality) — weekly Stage-0 creation (net + per SDR), WoW trend, and
   cohort-maturation S0→S1 / S0→closed-lost conversion (immature recent weeks are censored).
2. **Execution** (AE motion) — weekly AE assignments, S0→S1→S2→S3 funnel, **median** velocity,
   and a distribution-free *getting-faster/slower* test (Mann-Kendall + Theil-Sen, gated at n≥8).
3. **Forecast** (per AE) — deterministic weighted-$ scenarios (commit / most-likely / best /
   worst), coverage & gap-to-target, then a Claude agent that surfaces deals to discuss,
   anomalies, and deals that should be closed-lost — strictly downstream of the math.
4. **Cohorts** — deals cohorted by **create week**: stage-progression funnel, stage depth,
   conversions (0→1, 1→2, …) and median time between stages.
5. **Deals + Inspector** — a per-deal forensic view: stage-movement timeline, time-in-stage,
   activity counts by type, meetings held vs booked, days-since-touch, and a merged activity feed.
6. **Rep Scorecard** — a transparent **Working Score** (touch coverage, neglect, late-stage
   cadence, meeting discipline) to catch reps not working their deals.

All metrics are scoped to **Q2 2026** (quarter start `2026-04-01`, configurable) and are
**event-dated**: a metric counts in the ISO week the stage *event* happened.

## Setup

```bash
# 1. Put real credentials in .env (see .env.example). The provided .env already has them.
# 2. Create a venv that inherits the already-installed web stack, add the 2 new deps:
python3 -m venv .venv --system-site-packages
.venv/bin/python -m pip install anthropic aiosqlite tzdata pytest pytest-asyncio
# (or: .venv/bin/python -m pip install -r requirements.txt)

# 3. Run
./run.sh                      # -> http://localhost:8787
```

Then open **http://localhost:8787**, click **Sync now** to pull from HubSpot (~15s for ~870
deals; ~50–150 batched requests, no per-deal N+1), and explore. On the **Forecast** tab pick an
AE and click **Run forecast agent** to invoke Claude.

## How it works

- `app/hubspot/` — async HubSpot client (bearer auth, retry/backoff), deal search (pulls all
  16 `hs_v2_date_entered/exited_<stage>` props inline), and engagement fetch via v4 batch
  associations + v3 batch object reads (open deals + deals closed in the last 90d only).
- `app/sync/` — `events.py` derives a clean stage timeline (handles skips, back-movement,
  reopens); `runner.py` orchestrates the sync; `rollups.py` materializes per-deal activity +
  per-rep weekly rollups. Idempotent and phase-transactional.
- `app/metrics/` — `prepipeline`, `execution`, `cohorts`, `forecast`, `accountability`, `flags`,
  plus `stats.py` (median/IQR, Mann-Kendall, Theil-Sen) and `windows.py` (tz-aware ISO weeks).
- `app/agents/` — the Claude forecast agent: forced structured output, every `deal_id`
  validated against the input packet (hallucinations dropped). It never recomputes a dollar.
- `web/` — the SPA. `app.js` routes + sync controller + flags drawer + inspector modal.

## Configuration (Settings tab, persisted in SQLite)

Cadence targets (S0→S1 14d, S1→S3 20d, S4/S5 7d), stage win-probabilities + source
(house / hubspot / blend), per-AE quarter targets, and the owner roster/roles. Saving
recomputes metrics from the existing mirror — no re-fetch.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

Covers stage-timeline derivation (skip/stall/reopen), the statistics helpers, and the flag
rules + stage-order closure.
