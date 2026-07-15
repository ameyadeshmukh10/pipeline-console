"""Unit tests for the reworked Execution math (no DB).

The gate-conversion formula and the "or beyond" win-counting are the
trust-critical pieces, mirroring the corrected Pre-Pipeline conversion.
"""
from app.metrics.common import DealView
from app.metrics.execution import (compute_entered, compute_gate,
                                   compute_velocity, funnel_summary, owner_key)
from app.metrics.windows import (iso_week_key, iso_weeks_in_range, parse_ts,
                                 window_end_dt, window_start_dt)

QSTART = window_start_dt()
QEND = window_end_dt()
NOW = parse_ts("2026-06-30T00:00:00Z")
WEEKS = iso_weeks_in_range(QSTART, QEND)


def dt(s):
    return parse_ts(s)


def mk(owner, entries, is_open=1, current_order=1):
    return DealView(
        deal_id=f"d{id(entries)}", owner_id=owner, created_by=owner,
        current_order=current_order, is_open=is_open, amount=None, hs_prob=None,
        createdate=dt("2026-04-01T00:00:00Z"), closedate=None,
        create_iso_year=None, create_iso_week=None, dealstage=None, dealname="x",
        entries={o: dt(t) for o, t in entries.items()},
    )


def widx(ts):
    return WEEKS.index(iso_week_key(dt(ts)))


# --------------------------------------------------------------------------- #
# Objective 1 — entered-stage counts
# --------------------------------------------------------------------------- #
def test_entered_counts_by_actual_entry_week_skip_not_counted():
    entered_s2 = mk("A", {1: "2026-05-01T00:00:00Z", 2: "2026-05-04T00:00:00Z"}, current_order=2)
    skip = mk("B", {1: "2026-05-01T00:00:00Z", 3: "2026-05-05T00:00:00Z"}, current_order=3)  # no S2
    r = compute_entered([entered_s2, skip], WEEKS, 2, QSTART, QEND, NOW)
    i = widx("2026-05-04T00:00:00Z")
    assert r["aggregate"][i] == 1
    assert r["by_owner"]["A"][i] == 1
    assert "B" not in r["by_owner"]            # B never entered S2


def test_entered_won_counted_and_pre_quarter_excluded():
    won = mk("A", {1: "2026-05-01T00:00:00Z", 6: "2026-05-10T00:00:00Z"}, is_open=0, current_order=6)
    assert compute_entered([won], WEEKS, 6, QSTART, QEND, NOW)["aggregate"][widx("2026-05-10T00:00:00Z")] == 1
    pre = mk("A", {1: "2026-03-31T00:00:00Z"}, current_order=1)   # before Q2
    assert sum(compute_entered([pre], WEEKS, 1, QSTART, QEND, NOW)["aggregate"]) == 0


# --------------------------------------------------------------------------- #
# Objective 2 — velocity (median + mean), pooling
# --------------------------------------------------------------------------- #
def test_velocity_days_median_and_mean():
    base = "2026-05-11T00:00:00Z"
    ds = [mk("A", {1: s, 2: base}, current_order=2)
          for s in ("2026-05-10T00:00:00Z", "2026-05-09T00:00:00Z", "2026-05-02T00:00:00Z")]  # 1,2,9 days
    r = compute_velocity(ds, WEEKS, 1, QSTART, QEND)
    i = widx(base)
    assert r["aggregate"]["median"]["weekly"][i]["avg"] == 2.0    # median(1,2,9)
    assert abs(r["aggregate"]["mean"]["weekly"][i]["avg"] - 4.0) < 1e-9  # mean(1,2,9)
    assert r["aggregate"]["median"]["weekly"][i]["n"] == 3


def test_velocity_rolling_pools_raw_values_not_medians():
    d1 = mk("A", {1: "2026-04-24T00:00:00Z", 2: "2026-05-04T00:00:00Z"}, current_order=2)  # 10d, wk1
    d2 = mk("A", {1: "2026-05-09T00:00:00Z", 2: "2026-05-11T00:00:00Z"}, current_order=2)  # 2d, wk2
    r = compute_velocity([d1, d2], WEEKS, 1, QSTART, QEND)
    i2 = widx("2026-05-11T00:00:00Z")
    assert r["aggregate"]["median"]["rolling4"][i2]["avg"] == 6.0   # median([10,2]) pooled
    assert r["aggregate"]["median"]["rolling4"][i2]["n"] == 2


def test_velocity_group_pooling_concatenates():
    base = "2026-05-11T00:00:00Z"
    a = mk("A", {1: "2026-05-01T00:00:00Z", 2: base}, current_order=2)  # 10d
    b = mk("B", {1: "2026-05-09T00:00:00Z", 2: base}, current_order=2)  # 2d
    groups = [{"id": "g", "name": "G", "member_ids": ["A", "B"]}]
    cell = compute_velocity([a, b], WEEKS, 1, QSTART, QEND, groups)["by_group"]["g"]["median"]["weekly"][widx(base)]
    assert cell["avg"] == 6.0 and cell["n"] == 2


# --------------------------------------------------------------------------- #
# Objective 3 — gate conversion (the formula + "or beyond")
# --------------------------------------------------------------------------- #
def test_gate_formula_and_or_beyond_counts_won():
    base = "2026-05-04T00:00:00Z"
    a = mk("A", {1: base, 2: "2026-05-06T00:00:00Z"}, current_order=2)            # advanced
    b = mk("B", {1: base, 3: "2026-05-07T00:00:00Z"}, current_order=3)            # skipped S2 → reached S2+ → advanced
    c = mk("C", {1: base, 6: "2026-05-20T00:00:00Z"}, is_open=0, current_order=6)  # WON → advanced (or beyond)
    d = mk("D", {1: base, 7: "2026-05-08T00:00:00Z"}, is_open=0, current_order=7)  # lost at S1
    e = mk("E", {1: base}, current_order=1)                                        # still open at S1 → excluded
    cell = compute_gate([a, b, c, d, e], WEEKS, 1, QSTART, QEND)["aggregate"]["weekly"][widx(base)]
    assert cell["advanced"] == 3 and cell["lost"] == 1 and cell["denom"] == 4
    assert abs(cell["rate"] - 0.75) < 1e-9


def test_gate_5_to_won_is_win_rate():
    base = "2026-05-04T00:00:00Z"
    won = mk("A", {5: base, 6: "2026-05-10T00:00:00Z"}, is_open=0, current_order=6)
    lost = mk("B", {5: base, 7: "2026-05-11T00:00:00Z"}, is_open=0, current_order=7)
    open5 = mk("C", {5: base}, current_order=5)
    cell = compute_gate([won, lost, open5], WEEKS, 5, QSTART, QEND)["aggregate"]["weekly"][widx(base)]
    assert cell["advanced"] == 1 and cell["lost"] == 1 and cell["rate"] == 0.5


def test_gate_group_pooling_sums_num_den():
    base = "2026-05-04T00:00:00Z"
    a = mk("A", {1: base, 2: "2026-05-06T00:00:00Z"}, current_order=2)
    b = mk("B", {1: base, 7: "2026-05-07T00:00:00Z"}, is_open=0, current_order=7)
    groups = [{"id": "g", "name": "G", "member_ids": ["A", "B"]}]
    cell = compute_gate([a, b], WEEKS, 1, QSTART, QEND, groups)["by_group"]["g"]["weekly"][widx(base)]
    assert cell["advanced"] == 1 and cell["lost"] == 1 and abs(cell["rate"] - 0.5) < 1e-9


def test_funnel_full_product():
    base0 = "2026-05-01T00:00:00Z"
    win = mk("A", {0: base0, 1: "2026-05-02T00:00:00Z", 2: "2026-05-03T00:00:00Z",
                   3: "2026-05-04T00:00:00Z", 4: "2026-05-05T00:00:00Z",
                   5: "2026-05-06T00:00:00Z", 6: "2026-05-07T00:00:00Z"}, is_open=0, current_order=6)
    lost_s1 = mk("B", {0: base0, 1: "2026-05-02T00:00:00Z", 7: "2026-05-03T00:00:00Z"}, is_open=0, current_order=7)
    fs = funnel_summary([win, lost_s1], QSTART, QEND)
    g = {x["key"]: x for x in fs["gates"]}
    assert g["S0_S1"]["rate"] == 1.0       # both entered S0, both reached S1
    assert g["S1_S2"]["rate"] == 0.5       # win advanced, lost_s1 lost at S1
    assert g["S2_S3"]["rate"] == 1.0       # only win entered S2, advanced
    assert abs(fs["full_funnel"] - 0.5) < 1e-9   # 1 × 0.5 × 1 × 1 × 1 × 1


def test_owner_key_unassigned():
    assert owner_key(mk(None, {1: "2026-05-04T00:00:00Z"})) == "unassigned"
