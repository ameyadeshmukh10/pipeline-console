"""Unit tests for the flag rule engine + stage-order closure helpers."""
from datetime import datetime, timedelta, timezone

from app.metrics.common import DealView, RoleSets
from app.metrics.flags import compute_flags

NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)
ROLES = RoleSets(
    sdr={"sdr1"}, ae={"ae1"}, se={"se1"}, never_owns={"nick"},
    roster={"sdr1", "ae1", "se1", "nick"}, s0_allowed={"sdr1"},
    names={"sdr1": "SDR One", "ae1": "AE One", "se1": "SE One", "nick": "Nick DiCello"},
)
SETTINGS = {"s0_to_s1_target_days": 14, "s1_to_s3_target_days": 20, "late_stage_activity_days": 7}


def mk(owner_id, order, dealstage, entries, is_open=1, amount=1000.0,
       closedate=NOW + timedelta(days=10)):
    return DealView(deal_id="d", owner_id=owner_id, created_by=owner_id, current_order=order,
                    is_open=is_open, amount=amount, hs_prob=None, createdate=NOW - timedelta(days=40),
                    closedate=closedate, create_iso_year=2026, create_iso_week=20,
                    dealstage=dealstage, dealname="Deal", entries=dict(entries))


def codes(flags):
    return {f["code"] for f in flags}


def test_stage0_aging_and_non_sdr():
    v = mk("ae1", 0, "1699505395", {0: NOW - timedelta(days=30)})
    c = codes(compute_flags(v, None, ROLES, SETTINGS, NOW))
    assert "STAGE0_AGING" in c          # 30d > 14d
    assert "STAGE0_NON_SDR" in c        # AE owns a Stage 0
    assert "OWNER_OFF_ROSTER" not in c  # ae1 is in the roster


def test_fresh_sdr_stage0_clean():
    v = mk("sdr1", 0, "1699505395", {0: NOW - timedelta(days=3)})
    assert codes(compute_flags(v, None, ROLES, SETTINGS, NOW)) == set()


def test_nick_always_flagged():
    v = mk("nick", 2, "qualifiedtobuy", {0: NOW - timedelta(days=20), 1: NOW - timedelta(days=15), 2: NOW - timedelta(days=10)})
    assert "NICK_OWNS" in codes(compute_flags(v, None, ROLES, SETTINGS, NOW))


def test_late_stage_silent_critical():
    entries = {0: NOW - timedelta(days=40), 1: NOW - timedelta(days=30), 4: NOW - timedelta(days=10)}
    v = mk("ae1", 4, "5443822812", entries)
    c = compute_flags(v, {"days_since_last_activity": 12}, ROLES, SETTINGS, NOW)
    silent = [f for f in c if f["code"] == "LATE_STAGE_SILENT"]
    assert silent and silent[0]["severity"] == "critical"


def test_s1_s3_slow_open():
    entries = {0: NOW - timedelta(days=40), 1: NOW - timedelta(days=30)}  # in S1 30d, no S3
    v = mk("ae1", 1, "appointmentscheduled", entries)
    assert "S1_S3_SLOW" in codes(compute_flags(v, None, ROLES, SETTINGS, NOW))


def test_off_roster_and_stale_closedate():
    v = mk("ghost", 1, "appointmentscheduled",
           {0: NOW - timedelta(days=10), 1: NOW - timedelta(days=5)},
           closedate=NOW - timedelta(days=2))
    c = codes(compute_flags(v, None, ROLES, SETTINGS, NOW))
    assert "OWNER_OFF_ROSTER" in c and "CLOSEDATE_STALE" in c


def test_closure_helpers_with_skip():
    # entered S0 then jumped to S2 (skipped S1)
    v = mk("ae1", 2, "qualifiedtobuy", {0: NOW - timedelta(days=10), 2: NOW - timedelta(days=8)})
    assert v.ever_reached(1) is True          # reaching S2 implies S1 passed
    assert v.first_reach(1) == v.entries[2]    # earliest stage >=1 is the S2 entry
    assert v.max_pipeline_order == 2
    assert v.ever_reached(3) is False
