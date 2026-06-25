# Pipeline Console — Methodology, Logic & Decisions

This document explains **what every report computes, how, and why** — the data
model, the math, and the rationale behind the design decisions. It is the
companion to the [README](../README.md) (which covers setup/run/deploy).

The goal of the whole tool is to make HubSpot's pipeline *legible*: who creates
what, how deals actually move, whether reps are working them, and what the real
forecast is — computed rigorously enough to trust.

---

## Guiding principles

These show up everywhere; read them first.

1. **Deterministic math first, LLM strictly downstream.** Every number — counts,
   rates, days, dollars, probabilities, scores — is computed in pure, unit-tested
   Python and *frozen* before any LLM sees it. Claude narrates and judges; it
   never computes or restates a number. This is the single most important choice
   for trust and reproducibility.
2. **Event-dated, not snapshot.** A metric counts in the ISO week the underlying
   *stage event* happened (entered S0, entered S1, …), reconstructed from
   HubSpot's stage-history timestamps — not "what stage is it in today."
3. **Medians over means for durations.** Sales-cycle data is right-skewed with
   extreme outliers (a deal sat 49 days in Stage 0; stage-skips produce 3-second
   transitions) and tiny per-rep-per-week samples. A bare mean lies. We report
   medians with IQR + n, except where a user explicitly asked for "average."
4. **Be honest about small n.** Weekly per-rep counts are 0–5. We suppress rates
   on tiny denominators, gate trend tests at a minimum n, censor immature
   cohorts, and pool windows to manufacture sample size — rather than drawing a
   confident line through noise.
5. **Pooling, never averaging-of-averages.** Any rolled-up rate or mean pools the
   underlying *components* (Σnumerator / Σdenominator, Σdays / Σn) — never the
   mean of already-computed rates. This is what makes group/rolling/cumulative
   math correct.
6. **Identity rigor.** People in HubSpot have multiple IDs and lifecycles
   (owner-id vs user-id, active vs archived). We resolve all of them so a real
   name always shows and a deal is attributed to the right person.

---

## 1. Data foundation

### 1.1 Pipeline & stages
Only the HubSpot pipeline `default` ("Sales Pipeline") is in scope. Its 8
in-use stages, with a numeric **order** that drives all funnel/closure logic:

| order | id | label | class |
|---|---|---|---|
| 0 | `1699505395` | Stage 0 — Deal Qualification | open |
| 1 | `appointmentscheduled` | Stage 1 — Project Discovery | open |
| 2 | `qualifiedtobuy` | Stage 2 — Use Case Validation | open |
| 3 | `2344069311` | Stage 3 — Proposal | open |
| 4 | `5443822812` | Stage 4 — Contract Review | open |
| 5 | `contractsent` | Stage 5 — Contract Sent | open |
| 6 | `closedwon` | Closed Won | won |
| 7 | `closedlost` | Closed Lost | lost |

Two legacy `(to delete)` stages (`presentationscheduled`,
`decisionmakerboughtin`) are ignored entirely. All stage ids/labels/order live in
one place (`app/constants.py`) so nothing hardcodes a HubSpot id twice.

### 1.2 Stage timeline — the backbone
Every metric about *movement* needs to know when a deal entered/exited each
stage. HubSpot exposes this as calculated properties
`hs_v2_date_entered_<stageId>` / `hs_v2_date_exited_<stageId>` (16 of them for our
8 stages), returned **inline in the deal search** — so reconstructing a full
timeline needs **zero per-deal calls**.

`app/sync/events.py` turns these into a clean per-(deal, stage) event log, with
deliberate handling of real-world messiness:
- **Stage skips**: a stage with no `entered` timestamp simply has no event; we do
  not synthesize one. Funnel/conversion logic uses *stage-order closure* instead
  (below), so a deal that jumped S0→S2 still counts as having "reached S1."
- **Latest-entry-wins**: the v2 properties store only the most recent entry per
  stage, so a deal that bounced into a stage twice collapses to its last visit.
  This is an accepted, documented limitation — the raw `dealstage` property
  *history* returns 0 entries in this portal, so the v2 props are the only
  source. Back-movement is still *detected* (for the reopen flag) by checking for
  non-monotonic entry order over time.
- **Idempotent**: every sync deletes a deal's events and rebuilds them, so reruns
  are deterministic and self-healing.

**Stage-order closure** (`DealView.first_reach`, `ever_reached`): "reached stage
k" means *the earliest entry into any stage of order ≥ k* (won counts, lost does
not). This is how we correctly credit skip-deals and how "advanced past S0"
includes deals now sitting in S3 or even Closed-Won.

### 1.3 Identity & attribution — owner vs creator, and the ID maze
Two different questions need two different people:
- **Who owns it now** (`hubspot_owner_id`) — used by Execution, Forecast,
  flags, the scorecard.
- **Who created it** (`hs_created_by_user_id`) — used by Pre-Pipeline, because
  SDR/creation quality is about who *sourced* the deal, not who holds it now (an
  SDR creates a Stage 0, an AE later owns it).

Resolving these to **names** is genuinely fiddly and we handle three traps:
1. **Archived ex-reps.** `/crm/v3/owners` omits archived owners by default, so
   former SDRs (Michele Locker, Hanna Liakh, Nate Sprecher, …) rendered as
   "Other (id)". Fix: the sync fetches owners with **both** `archived=false` and
   `archived=true`.
2. **User-id vs owner-id.** `hs_created_by_user_id` is a *user* id, which usually
   equals the owner id — but not always (an archived owner can have a different
   user id, or none). Some creators (e.g. `65252438` = Hanna again) have a user
   id with **no** owner record. Fix: the sync also pulls `/settings/v3/users` and
   `INSERT OR IGNORE`s them into the owners table to fill the gaps (real owner
   rows always win).
3. **Unknown.** `created_by IS NULL` → bucketed as "Unknown" rather than dropped.

Name resolution chain: owners table (incl. archived + users) → roster display
name → `Other (id)`. First names are used on chips/legends (with a last initial
on collisions); full names in tables/tooltips.

### 1.4 Roster & roles
Roles are a **set per owner** (a person can be several things), seeded in
`app/constants.py`, editable in Settings:

| owner | roles |
|---|---|
| Cam Barcus | SDR (creates & owns Stage 0) |
| Morgan Schneider | SDR |
| Nicole Cierpial | AE (owns S1+) |
| Cole Allemong | AE |
| Lucas Cowell | SE — may create a Stage 0, never own past it |
| Nick DiCello | never owns (always flagged) |

Derived sets: `SDR_IDS`, `AE_IDS`, `S0_ALLOWED_IDS` (SDRs + SE — who may legally
own a Stage 0 without flagging). **Attribution is by the stage the event occurred
in, not the owner alone** — e.g. a Stage-0 "created" event is credited to the
creator even if an AE owns the deal now. Deals owned/created by anyone outside
the roster are still counted (they're real) but bucketed "Other/Unknown" and, for
owned deals, flagged.

### 1.5 Sync engine — fast and N+1-free
One manual "Sync now" pass (`app/sync/runner.py`), bounded concurrency (~5),
phase-transactional and idempotent, ~50–150 HTTP calls for ~870 deals in seconds:
1. Owners (active + archived) + users → owners table.
2. Deals: paginated search over `pipeline=default`, requesting flat props **plus
   all 16 hs_v2 stage props inline** → upsert + derive the event/transition log
   (0 extra HTTP).
3. Engagements: **only** for open deals + deals closed in the last 90d, via v4
   batch-association reads (per-deal counts come free from the association-list
   length) then v3 batch object reads for timestamps/owner/direction. Never a
   per-deal loop.
4. Materialize `deal_activity_rollup` (per deal) and `rep_week_rollup` (per
   owner×week) so the UI never computes on the hot path.

### 1.6 Time & scoping
- **Window**: Q2 2026 (`quarter_start = 2026-04-01`, configurable). `quarter_end`
  is derived (3 months − 1s).
- **ISO weeks in a business timezone** (`America/New_York`, configurable). All
  storage is UTC; week bucketing converts to the business tz first so an event
  near Sunday/Monday midnight lands in a consistent week everywhere. (`tzdata` is
  a hard dependency — without it `zoneinfo` silently falls back to UTC and every
  week boundary shifts.)
- **Two scoping lenses, used deliberately:**
  - **Event-dated** (Execution, cohort transitions): a metric counts in the week
    its stage event happened; an event on/after Apr 1 counts even if the deal was
    created earlier.
  - **Createdate-scoped** (Pre-Pipeline): only deals whose `createdate` is in the
    quarter, because "how many Stage-0s did we generate this quarter" is a
    question about *this quarter's vintage* of deals.

---

## 2. Report 1 — Pre-Pipeline (creator / SDR quality)

**Question:** are we generating enough Stage-0s, are they good, and who's
sourcing them? `app/metrics/prepipeline.py`, all over `createdate ∈ Q2`,
attributed by **creator**, including **every** creator (roster, off-roster,
archived). The math lives in pure functions over `DealView`s so it's unit-tested
without a DB.

### 2.1 Weekly Stage-0 Created + trend views
Count deals by `createdate` ISO-week and creator → per-creator weekly arrays + an
aggregate. The objective is to see whether generation is **stable / rising /
declining**, and the chart offers three lenses (`series.pooled_count_series`):

- **Weekly** — raw counts. Honest but jumpy (a holiday, a rep on PTO). Good for
  spotting specific weeks, bad for trend.
- **4-week rolling** — trailing mean of the last 4 weekly counts. *This is the
  trend workhorse:* it strips operational noise so the real shape shows. (On the
  real data this revealed a mid-quarter peak around W19–W20 followed by a
  decline — an arc a single trend number hid.)
- **Cumulative** — running total, drawn against a dashed **pace line** (constant
  average weekly rate over complete weeks). Because a cumulative count only ever
  rises, you read it by deviation from pace: pulling above = accelerating,
  flattening below = slowing.

A **fitted trend line** (`series.fit_line`) overlays on demand. Two deliberate
choices keep it honest:
- It's fit over **complete weeks only** — the partial current week is excluded so
  a half-finished week can't fake a downturn.
- It's **tied to the significance verdict**: when the net trend is *flat /
  insufficient* (Mann-Kendall not significant), the line is drawn **horizontal at
  the median** (a baseline), not as a misleading slope through hump-shaped data;
  it only slopes when the trend is statistically real (robust Theil-Sen slope).
  This keeps the drawn line and the "flat" trend tag consistent.

### 2.2 Speed to Stage 1
**Question:** of the Stage-0s a rep sourced, how fast do they advance to Stage 1?
For each Q2-created deal that reached S1, compute `days = entered_S1 −
entered_S0`, and bucket it by the **week it entered S1** (the transition week, not
the creation week), attributed to the creator. Per (creator, week) we keep the
components `{Σdays, n}`.

The chart shows the pooled **aggregate average** in three modes you can overlay
(Weekly / 4-wk rolling / Cumulative); rolling and cumulative **pool the underlying
deal day-values** (Σdays / Σn), which is the statistically correct way to
manufacture the sample size a single week lacks. The granular per-creator table
shows each week's `avg (n)` so the denominator is always visible. (We use the
arithmetic **mean** here because the request was explicitly "average"; a
median-based variant would be more outlier-robust and is a known toggle to add.)

### 2.3 Corrected S0→S1 conversion
This replaced an earlier rate that divided by *all* S0 cohort members — which is
why S0→S1 conversion and S0→Closed-Lost appeared to **decline together** when they
should be inversely correlated. The corrected definition (cohorted by createdate
week):

```
S0→S1 rate = advanced ÷ (advanced + closed-lost-at-S0)
```

- **advanced** = ever reached Stage 1+ (closure-aware — counts even if the deal is
  now in S3 or Closed-Won; it cleared the gate).
- **closed-lost-at-S0** = closed lost AND never reached S1 (a failed Stage 0).
- **still-open Stage-0 deals are excluded entirely** — they haven't *resolved*
  yet, exactly as you'd never count an open deal in a win rate.

So advanced-share + lost-share = 1 by construction (the two are now properly
inverse). Worked invariant (a unit test): 50 advanced + 30 lost-at-S0 + 20
still-open → 50 ÷ 80 = **62.5%**.

The same three smoothing modes apply, here pooling numerator/denominator
(Σadvanced / Σ(advanced+lost)). The dropdown carries written tooltips explaining
the **sample-size tradeoff** of each, because for a *rate* it's decisive:
- *Isolated weekly* — needs ~80–100 resolved deals/week to trust a point at ±10;
  most funnels never hit that, so single weeks are noisy/decorative.
- *4-week rolling* — pools ~100 resolved deals across 4 weeks (~25/week), the
  realistic sweet spot; it manufactures the sample size you don't have weekly.
- *Cumulative QTD* — n grows every week so it's statistically tight by mid-quarter;
  the tradeoff isn't sample size, it's **staleness** (increasingly insensitive to
  recent change).

### 2.4 Creator groups
You can define **persistent named groups** of creators (Settings →
`settings.creator_groups`) and assess them pooled. A group is just another
poolable series, and pooling is **mathematically exact** because all three
metrics are linear in their components:
- created counts: `Σ member weekly counts`
- speed: `Σ member Σdays / Σ member n`
- conversion: `Σ member advanced / Σ member (advanced + lost)`

Groups are **purely additive** (a hard requirement): a group shows as a
toggleable line (with its own significance-gated trend line) in the Created chart
and as a pinned pooled row in every section's granular table — while the existing
per-creator lines, toggles, speed overlay, and conversion dropdown are untouched.
A no-regression unit test asserts the per-creator/aggregate outputs are identical
when no groups exist.

---

## 3. Report 2 — Execution (AE motion)

`app/metrics/execution.py`. Event-dated.

- **Weekly AE assignment** = deals whose **entry into Stage 1** falls in week W
  (skip-deals counted at their earliest entry into any stage ≥ S1 — the
  handoff). Attributed to the owner *at that event*. Net + per AE.
- **Progression funnel** S0→S1→S2→S3: weekly forward stage-entry counts, **closure
  aware** (entering S2 implies S1 passage), shown weekly and as a cumulative Q2
  funnel with conversion % and median days per transition.
- **Velocity** = **median** days (with IQR + n) for time-in-stage, S0→S1, and
  S1→S3 (S3 via closure). Only *completed* transitions feed the medians; open
  deals are tracked separately for aging — with an explicit survivorship caveat,
  since excluding still-stuck deals biases velocity to look faster than reality.
- **Getting faster / slower** — we do **not** diff weekly medians (per-AE-week n is
  0–5, pure noise). Instead, order completed transitions by their source-stage
  cohort week and run a distribution-free **Mann-Kendall** test (direction +
  significance) plus a robust **Theil-Sen** slope (days/week). It's **gated at
  n ≥ 8**: below that it reports "insufficient data — N transitions" rather than a
  direction. Per AE (gated) and pooled total.

---

## 4. Report 3 — Per-AE Forecast

`app/metrics/forecast.py` (deterministic) + `app/agents/forecast_agent.py` (LLM).

### 4.1 Deterministic scenarios
For an AE's open deals (stages 1–5; Stage 0 shown separately as early pipeline),
each deal gets a `p_win` from the editable **house stage-probability map**
(default S1 .10 / S2 .25 / S3 .45 / S4 .65 / S5 .85; S0 .05 and excluded from the
$ forecast) and `weighted_amount = amount × p_win`. (HubSpot's own
`hs_deal_stage_probability` is also ingested for a reconciliation view; a
`probability_source` toggle picks house / hubspot / blend.)

The four scenarios form a **monotonic ladder** (`worst ≤ commit`, `most_likely ≤
best`), on top of banked `closed_won_qtd`:

| scenario | definition |
|---|---|
| **Worst** | + full amount of *actively-worked* S5 deals only |
| **Commit** | + full amount of *actively-worked* S4 & S5 deals |
| **Most-likely** | + Σ weighted_amount over all open S1–S5 (expected value) |
| **Best** | + early pipeline (S1/S2) at *weighted* value + every S3+ deal at *full* value |

> **Why "best" is defined this way** — a real bug this fixed: an earlier "best =
> all S3+ deals that close *this quarter*" returned **$25k** for an AE whose
> most-likely was **$251k**, because all her S3 deals close *next* quarter and the
> close-date gate zeroed them. A best case below most-likely is nonsense. The fix:
> drop the close-date gate (this pipeline largely closes next quarter; close date
> is shown per deal instead) and define best as *most-likely with the late-stage
> deals lifted from weighted to full value* — guaranteeing `best ≥ most_likely`.

Each open deal also gets a deterministic **scenario tag** (Commit = active S4/S5;
Best = S3 or a stalled late-stage deal; Upside = S1/S2) so the table shows which
bucket every deal sits in. **Coverage/gap**: `open_pipeline ÷ target`,
`most_likely ÷ target`, `gap = target − most_likely`; per-AE target from Settings
with an org fallback; health bands by fixed thresholds.

### 4.2 The LLM agent
Input is one per-AE JSON *packet* containing **only** the deterministic facts (the
scenarios + contributing deal-ids, coverage/gap, every open deal with its flags,
stage, days-in-stage, engagement recency, and velocity context). Output is forced
**structured JSON**: deals to discuss, anomalies/issues, deals that *should be
closed-lost* (rationale grounded only in provided facts), and an optional per-deal
commit/best/omit call. Guardrails: reason only over provided facts, cite real
deal-ids, never invent or recompute a number. **Every `deal_id` the model returns
is validated against the packet and dropped if hallucinated.** The static rules
prompt is cached; only the per-AE packet varies.

---

## 5. Report 4 — Deal visibility & rep accountability

`app/metrics/accountability.py` + the flag engine `app/metrics/flags.py`. This is
the "see what HubSpot hides" surface.

### 5.1 Per-deal inspector
Stage-movement timeline (entered/exited, duration, target overlay, skip/reopen
markers), current time-in-stage, total cycle time; activity counts by type
**total and since entering the current stage**; meeting cadence (avg gap, last
meeting, **held vs booked** — held = outcome COMPLETED or start-time-past & not
canceled/no-show); inbound vs outbound; **who logged it** (% by the owner vs
others/automation); and a **merged chronological feed** of engagements +
stage-change markers so you read activity *relative to movement*.

### 5.2 Flags (deterministic rule engine)
Each is a pure `(deal, activity, roles, settings, now) → flags` check with an
exact fact string. Hard hygiene flags:

| code | condition | severity |
|---|---|---|
| `STAGE0_AGING` | open in S0 and now − S0-entry > 14d | warning |
| `STAGE0_NON_SDR` | open in S0 owned by someone not in the SDR/SE set | warning |
| `S1_S3_SLOW` | reached S3 with S1→S3 > 20d, OR open & >20d since S1 not yet at S3 | warning |
| `LATE_STAGE_SILENT` | open S4/S5 with no engagement in 7d (or none ever) | **critical** |
| `NICK_OWNS` | owned by a "never-owns" person (Nick DiCello) | **critical** |
| `OWNER_OFF_ROSTER` | open deal owned by someone outside the roster | warning |
| `NO_AMOUNT` | open S3+ deal with no amount (can't be weighted) | warning |
| `CLOSEDATE_STALE` | open deal with a missing or past close date | warning |
| `SE_OWNS` | the SE owns an open deal past Stage 0 | warning |
| `LOST_FROM_S0` | closed lost without ever reaching S1 (SDR-quality signal) | info |
| `REOPENED` | stage order moved backward over time (reopen/regression) | info |

Targets (14/20/7 days) are Settings-driven. Flags are scoped to the window (open
deals + deals closed in Q2) and sorted by worst severity.

### 5.3 Rep scorecard & Working Score
Over each rep's open deals + a trailing window: open deals & value, **pct touched
in 7d**, activities/deal/week, **neglect rate** (deals with any hard flag ÷ open),
hard-flag breakdown, meetings held/booked, stage-aging vs target, stage-jumper
count. These roll into a transparent **Working Score (0–100)**:

```
score = 100 × ( 0.40·touch_coverage
              + 0.25·(1 − neglect_rate)
              + 0.20·late_stage_cadence_compliance
              + 0.15·meeting_discipline )
```

It's always shown **with its component breakdown and the contributing flags** —
never a black box — and relative ("soft") signals are benchmarked against **this
team's medians**, not an arbitrary global constant. Edge cases handled: bulk/
marketing emails flagged `is_automated` and **excluded by default** (raw email
count never drives the score); a multi-deal engagement counts **distinct** at the
rep level; activity logged by a non-owner counts for the logger's cadence but
still satisfies "the deal got worked"; closed deals are excluded from "neglected".

---

## 6. Report 5 — Cohort analytics (by create week)

`app/metrics/cohorts.py`. Deliberately a **createdate** lens (distinct from the
event-dated weekly reports): group deals into calendar-week cohorts by
`createdate` and ask how that *vintage* progressed.

Per cohort: size; a **stage-progression funnel** (% that ever reached each stage,
closure-aware) + Lost%; **stage depth** (furthest stage reached, distribution);
stepwise **conversions** (0→1, 1→2, …); and **median time between stages** (with
IQR + n). Recent create-weeks are **censored** (shaded "still maturing", excluded
from cross-cohort trend claims) because their deals haven't had time to progress.
A "trend across cohorts" check (e.g. did S0→S1 lift as the quarter went on) uses
the same robust Theil-Sen/Mann-Kendall machinery.

---

## 7. Cross-cutting statistical decisions (recap)

| Decision | Where | Why |
|---|---|---|
| Median + IQR for durations, never bare mean | Execution, cohorts, inspector | right-skewed data + outliers |
| Cohort-maturation conversion + censoring | Pre-Pipeline, cohorts | recent cohorts haven't resolved; counting them understates conversion |
| Exclude still-open S0 from S0→S1 rate | Pre-Pipeline | an unresolved deal is neither a win nor a loss |
| Pool components, not averages of rates | rolling/cumulative, groups | mean-of-rates is wrong; Σnum/Σden is right |
| Mann-Kendall + Theil-Sen, gated at n≥8 | Execution velocity, cohort trend | distribution-free, robust; refuse to call a direction on noise |
| Significance-gated trend line (flat → median baseline) | Pre-Pipeline created | the drawn line must agree with the verdict, not imply a fake slope |
| Suppress rates on tiny denominators | per-creator panels | a 1-of-2 rate is not 50% you can act on |
| Monotonic forecast ladder, no close-date gate | Forecast | a best case below most-likely is nonsense; pipeline closes next quarter |
| LLM downstream of frozen math, deal-ids validated | Forecast, scorecard | trust, reproducibility, no hallucinated deals/dollars |

---

## 8. Glossary

- **Stage-order closure** — "reached stage k" = earliest entry into any stage of
  order ≥ k (won counts, lost doesn't). Credits skip-deals correctly.
- **Event-dated** — bucketed by when the stage event happened, not current state.
- **Resolved (Stage 0)** — advanced to S1+ OR closed-lost-at-S0; *not* still-open.
- **Active (late-stage)** — engaged within the late-stage activity window (7d).
- **Pooling** — combining components (Σnum/Σden, Σdays/Σn) before computing the
  ratio/mean — used for rolling, cumulative, and group aggregation.
- **Maturation / censoring** — recent cohorts/weeks are marked immature and kept
  out of trend claims because their deals haven't had time to resolve.

---

## 9. Deferred / future work

> **⏳ Persist Settings (incl. creator groups) across Railway deploys.**
> Creator groups, cadence/probability/target settings, and the whole synced
> mirror live in the SQLite DB, which is **ephemeral** on Railway — they reset on
> every deploy/restart. To make groups (and all settings) stick, attach a Railway
> **Volume** mounted at `/data` and set `DATA_DIR=/data`. The code already
> supports this via the `DATA_DIR`/`DB_PATH` env override (`app/config.py`) — **no
> code change needed**, just the volume + env var. Revisit when ready.

Other known follow-ups:
- Group lines on the Speed/Conversion *charts* (today groups appear there as
  pooled table rows only, to avoid disrupting those charts' existing controls).
- A mean↔median toggle for Speed to S1.
- Owner-change history (HubSpot doesn't expose it via standard properties here,
  so owner attribution uses the current owner as a proxy).
- Migrate FastAPI startup from the deprecated `@app.on_event` to `lifespan`.
