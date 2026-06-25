"""Unit tests for stage-history derivation against real-world patterns."""
from datetime import datetime, timezone

from app.constants import S0, S1, S2, S3, entered_prop, exited_prop
from app.sync.events import derive_stage_history

NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=timezone.utc)


def test_digiplus_stall_then_skip():
    """DigiPlus: ~49 days in S0, then S1->S2 ~3s apart (skip-fast)."""
    props = {
        entered_prop(S0): "2026-03-05T14:58:26.211Z",
        exited_prop(S0): "2026-04-23T16:35:41.483Z",
        entered_prop(S1): "2026-04-23T16:35:41.483Z",
        entered_prop(S2): "2026-04-23T16:35:44.185Z",
    }
    events, transitions = derive_stage_history(props, current_stage_id=S2, now=NOW)

    assert [e["stage_order"] for e in events] == [0, 1, 2]
    s0 = events[0]
    assert s0["entered_iso_year"] == 2026 and s0["entered_iso_week"] == 10  # Mar 5
    assert abs(s0["dwell_seconds"] - 49 * 86400) < 2 * 86400  # ~49 days in S0
    assert events[2]["is_current"] == 1  # currently in S2

    t01 = transitions[0]
    assert (t01["from_stage_order"], t01["to_stage_order"]) == (0, 1)
    assert t01["transition_iso_week"] == 17  # Apr 23 -> W17
    t12 = transitions[1]
    assert (t12["from_stage_order"], t12["to_stage_order"]) == (1, 2)
    assert t12["gap_seconds"] in (2, 3) and t12["is_skip"] == 0  # 2.7s -> truncated


def test_real_stage_skip():
    """Created at S0, jumped straight to S2 (no S1 entry) -> is_skip transition."""
    props = {
        entered_prop(S0): "2026-04-10T10:00:00Z",
        exited_prop(S0): "2026-04-12T10:00:00Z",
        entered_prop(S2): "2026-04-12T10:00:00Z",
    }
    events, transitions = derive_stage_history(props, current_stage_id=S2, now=NOW)
    assert [e["stage_order"] for e in events] == [0, 2]
    assert len(transitions) == 1
    assert transitions[0]["is_skip"] == 1
    assert (transitions[0]["from_stage_order"], transitions[0]["to_stage_order"]) == (0, 2)


def test_back_movement_ordered_by_time():
    """A higher stage entered earlier than a lower stage (reopen/regression)
    must be ordered by time, never crash, and not produce negative dwell."""
    props = {
        entered_prop(S0): "2026-04-01T09:00:00Z",
        entered_prop(S2): "2026-04-05T09:00:00Z",
        entered_prop(S1): "2026-04-09T09:00:00Z",  # entered S1 AFTER S2 (back-move)
    }
    events, transitions = derive_stage_history(props, current_stage_id=S1, now=NOW)
    # time order: S0, S2, S1
    assert [e["stage_order"] for e in events] == [0, 2, 1]
    assert all(e["dwell_seconds"] >= 0 for e in events)
    # last transition goes backward 2 -> 1
    assert (transitions[-1]["from_stage_order"], transitions[-1]["to_stage_order"]) == (2, 1)


def test_open_stage_dwell_uses_now():
    props = {entered_prop(S0): "2026-06-01T00:00:00Z"}  # still open in S0
    events, _ = derive_stage_history(props, current_stage_id=S0, now=NOW)
    assert len(events) == 1 and events[0]["exited_at"] is None
    assert events[0]["dwell_seconds"] == int((NOW - datetime(2026, 6, 1, tzinfo=timezone.utc)).total_seconds())


def test_empty_props():
    events, transitions = derive_stage_history({}, current_stage_id=None, now=NOW)
    assert events == [] and transitions == []
