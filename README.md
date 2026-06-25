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

> 📐 **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)** — the full write-up of every report's
> logic, the math, and the rationale behind the decisions (attribution, conversion formula,
> trend views, forecast scenarios, flags, Working Score, statistical choices). Start there to
> understand *why* a number is what it is.

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

## Deploy to Railway

The repo is Railway-ready (`railway.json` pins the start command, healthcheck, and a single
replica; `.python-version` pins Python 3.12; `requirements.txt` is pinned + includes `tzdata`).

1. **Railway → New Project → Deploy from GitHub repo** → select `pipeline-console`.
2. In the service's **Variables**, set:
   - `HUBSPOT_ACCESS_TOKEN` — your HubSpot private-app token (required)
   - `ANTHROPIC_API_KEY` — required for the Forecast "Run agent" feature
   - *(optional)* `FORECAST_MODEL`, `CLAUDE_SOURCING_MODEL`, `PIPELINE_ID`, `QUARTER_START`,
     `TIMEZONE`, `HUBSPOT_BASE_URL` — all have sensible defaults.
   - **Do not set `PORT`** — Railway injects it; the app binds `0.0.0.0:$PORT`.
3. Deploy. Once the healthcheck (`/api/health`) passes, open the generated
   `*.up.railway.app` URL and click **Sync now** to pull from HubSpot.

**Notes**
- **Storage is ephemeral**: the SQLite mirror resets on every deploy/restart — just click
  **Sync now** again (~15s; HubSpot is the source of truth). To persist it, attach a Railway
  **Volume** mounted at e.g. `/data` and set `DATA_DIR=/data` (no code change needed).
- **Keep a single replica.** Sync state is in-process and the DB is one SQLite file, so do not
  scale to multiple replicas/instances (`numReplicas` is pinned to 1 in `railway.json`).
- Pushing to `main` triggers an automatic redeploy.
