"""Canonical, config-seeded reference data: the 8 relevant stages of the
HubSpot ``default`` pipeline, the owner roster + roles, and default settings.

This is the single source of truth. Report SQL and the frontend resolve stage
ids/labels/order and owner roles through here (or through the DB rows seeded
from here) so nothing hardcodes a HubSpot id in two places.
"""
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Stages (HubSpot pipeline id = "default", label "Sales Pipeline")
# Only these 8 are in use. The two "(to delete)" stages are ignored entirely.
# ---------------------------------------------------------------------------
IGNORED_STAGES = {"presentationscheduled", "decisionmakerboughtin"}

# order is the funnel position; is_open/won/lost classify terminal stages.
STAGES: List[Dict] = [
    {"stage_id": "1699505395",           "short": "S0",   "label": "Deal Qualification",  "order": 0, "is_open": 1, "is_won": 0, "is_lost": 0},
    {"stage_id": "appointmentscheduled", "short": "S1",   "label": "Project Discovery",   "order": 1, "is_open": 1, "is_won": 0, "is_lost": 0},
    {"stage_id": "qualifiedtobuy",       "short": "S2",   "label": "Use Case Validation", "order": 2, "is_open": 1, "is_won": 0, "is_lost": 0},
    {"stage_id": "2344069311",           "short": "S3",   "label": "Proposal",            "order": 3, "is_open": 1, "is_won": 0, "is_lost": 0},
    {"stage_id": "5443822812",           "short": "S4",   "label": "Contract Review",     "order": 4, "is_open": 1, "is_won": 0, "is_lost": 0},
    {"stage_id": "contractsent",         "short": "S5",   "label": "Contract Sent",       "order": 5, "is_open": 1, "is_won": 0, "is_lost": 0},
    {"stage_id": "closedwon",            "short": "WON",  "label": "Closed Won",          "order": 6, "is_open": 0, "is_won": 1, "is_lost": 0},
    {"stage_id": "closedlost",           "short": "LOST", "label": "Closed Lost",         "order": 7, "is_open": 0, "is_won": 0, "is_lost": 1},
]

STAGE_BY_ID: Dict[str, Dict] = {s["stage_id"]: s for s in STAGES}
STAGE_ORDER_BY_ID: Dict[str, int] = {s["stage_id"]: s["order"] for s in STAGES}
ORDER_TO_STAGE: Dict[int, Dict] = {s["order"]: s for s in STAGES}

# Stage id constants used across the code.
S0 = "1699505395"
S1 = "appointmentscheduled"
S2 = "qualifiedtobuy"
S3 = "2344069311"
S4 = "5443822812"
S5 = "contractsent"
WON = "closedwon"
LOST = "closedlost"

OPEN_STAGE_IDS = [s["stage_id"] for s in STAGES if s["is_open"]]            # S0..S5
PIPELINE_STAGE_IDS = [S0, S1, S2, S3, S4, S5]                              # ordered open stages
LATE_STAGE_IDS = [S4, S5]


def entered_prop(stage_id: str) -> str:
    """HubSpot v2 calculated property: when the deal entered this stage."""
    return f"hs_v2_date_entered_{stage_id}"


def exited_prop(stage_id: str) -> str:
    """HubSpot v2 calculated property: when the deal exited this stage."""
    return f"hs_v2_date_exited_{stage_id}"


# All 16 stage-history props (entered+exited for the 8 stages) requested inline
# in the deal search so stage timelines need zero per-deal calls.
STAGE_DATE_PROPS: List[str] = [entered_prop(s["stage_id"]) for s in STAGES] + [
    exited_prop(s["stage_id"]) for s in STAGES
]

# Flat deal props pulled every sync.
DEAL_FLAT_PROPS: List[str] = [
    "dealname", "dealstage", "pipeline", "hubspot_owner_id", "amount", "closedate",
    "createdate", "hs_lastmodifieddate", "notes_last_updated", "hs_deal_stage_probability",
    "days_to_close", "hs_is_closed", "hs_is_closed_won", "hs_object_id",
    "hs_created_by_user_id",  # the true CREATOR (userId == ownerId in this portal)
]
DEAL_ALL_PROPS: List[str] = DEAL_FLAT_PROPS + STAGE_DATE_PROPS

# ---------------------------------------------------------------------------
# Owner roster + roles (seeded; editable in Settings).
#   is_sdr / is_ae / is_se : role memberships (a person can hold several)
#   never_owns             : should never own a deal (always flag)
#   full_lifecycle         : legitimately owns from Stage 0 onward (Cole Brooker)
# ---------------------------------------------------------------------------
ROSTER: List[Dict] = [
    {"owner_id": "33139830", "display_name": "Cam Barcus",       "is_sdr": 1, "is_ae": 0, "is_se": 0, "never_owns": 0, "full_lifecycle": 0, "notes": "SDR — creates & owns Stage 0"},
    {"owner_id": "33139837", "display_name": "Morgan Schneider", "is_sdr": 1, "is_ae": 0, "is_se": 0, "never_owns": 0, "full_lifecycle": 0, "notes": "SDR — creates & owns Stage 0"},
    {"owner_id": "33146292", "display_name": "Lucas Cowell",     "is_sdr": 0, "is_ae": 0, "is_se": 1, "never_owns": 0, "full_lifecycle": 0, "notes": "SE — may create Stage 0; never own past S0 or a Stage 0 older than 2 weeks"},
    {"owner_id": "33146294", "display_name": "Nick DiCello",     "is_sdr": 0, "is_ae": 0, "is_se": 0, "never_owns": 1, "full_lifecycle": 0, "notes": "Never owns or creates a deal"},
    {"owner_id": "33139836", "display_name": "Nicole Cierpial",  "is_sdr": 0, "is_ae": 1, "is_se": 0, "never_owns": 0, "full_lifecycle": 0, "notes": "AE — owns deals Stage 1+"},
    {"owner_id": "33622637", "display_name": "Cole Allemong",    "is_sdr": 0, "is_ae": 1, "is_se": 0, "never_owns": 0, "full_lifecycle": 0, "notes": "AE — owns deals Stage 1+"},
]
ROSTER_BY_ID: Dict[str, Dict] = {r["owner_id"]: r for r in ROSTER}
ROSTER_IDS = set(ROSTER_BY_ID.keys())

# Owners who may own a Stage 0 without being flagged (SDRs + the SE).
S0_ALLOWED_IDS = {r["owner_id"] for r in ROSTER if r["is_sdr"] or r["is_se"]}
# True SDRs (Stage-0 creators).
SDR_IDS = {r["owner_id"] for r in ROSTER if r["is_sdr"]}
# AEs who own deals at Stage 1+.
AE_IDS = {r["owner_id"] for r in ROSTER if r["is_ae"]}


def owner_label(owner_id: Optional[str]) -> str:
    if not owner_id:
        return "Unassigned"
    r = ROSTER_BY_ID.get(owner_id)
    return r["display_name"] if r else f"Other/Unknown ({owner_id})"


# ---------------------------------------------------------------------------
# Default settings (seeded into the settings table; editable in the UI).
# ---------------------------------------------------------------------------
DEFAULT_STAGE_PROBABILITIES: Dict[str, float] = {
    S0: 0.05,   # not counted in $ forecast; shown as early pipeline
    S1: 0.10,
    S2: 0.25,
    S3: 0.45,
    S4: 0.65,
    S5: 0.85,
    WON: 1.0,
    LOST: 0.0,
}

DEFAULT_SETTINGS: Dict[str, object] = {
    "quarter_start": "2026-04-01",
    "s0_to_s1_target_days": 14,
    "s1_to_s3_target_days": 20,
    "late_stage_activity_days": 7,     # S4/S5 must show activity weekly
    "cold_open_days": 14,              # generic "cold" threshold for open deals
    "meeting_gap_days": 21,            # soft signal threshold
    "closed_engagement_window_days": 90,
    "stage_probabilities": DEFAULT_STAGE_PROBABILITIES,
    "probability_source": "house",     # house | hubspot | blend
    "ae_targets": {},                  # owner_id -> quarterly $ target
    "org_target": 0,
    "timezone": "America/New_York",
}
