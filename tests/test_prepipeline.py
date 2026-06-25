"""Unit tests for the reworked Pre-Pipeline math (no DB).

The conversion formula is the trust-critical piece: it must equal
advanced / (advanced + closed-lost-at-S0), with still-open S0 deals excluded.
"""
from datetime import timezone

from app.metrics.common import DealView
from app.metrics.prepipeline import (compute_conversion, compute_created,
                                     compute_speed_to_s1, creator_key, in_quarter)
from app.metrics.series import pooled_mean_series, pooled_rate_series
from app.metrics.windows import (iso_week_key, iso_weeks_in_range, parse_ts,
                                 quarter_end_dt, quarter_start_dt)

QSTART = quarter_start_dt()
QEND = quarter_end_dt()
WEEKS = iso_weeks_in_range(QSTART, QEND)


def dt(s):
    return parse_ts(s)


def mk(cid, create, entries, is_open=1, current_order=0):
    """Build a DealView. entries = {stage_order: 'iso ts'}."""
    return DealView(
        deal_id=f"d{id(entries)}", owner_id=cid, created_by=cid,
        current_order=current_order, is_open=is_open, amount=None, hs_prob=None,
        createdate=dt(create), closedate=None, create_iso_year=None,
        create_iso_week=None, dealstage=None, dealname="x",
        entries={o: dt(t) for o, t in entries.items()},
    )


def wk_index(ts):
    return WEEKS.index(iso_week_key(dt(ts)))


# --------------------------------------------------------------------------- #
# Conversion — the trust fix
# --------------------------------------------------------------------------- #
def test_conversion_62_5_percent_excludes_open_s0():
    """50 advanced + 30 lost-at-S0 + 20 still-open-S0 -> 50/80 = 62.5%."""
    create = "2026-05-04T12:00:00Z"  # a Monday, single cohort week
    deals = []
    for _ in range(50):  # advanced: reached S1
        deals.append(mk("A", create, {0: create, 1: "2026-05-06T12:00:00Z"},
                        is_open=1, current_order=1))
    for _ in range(30):  # closed-lost at S0 (never reached S1)
        deals.append(mk("A", create, {0: create, 7: "2026-05-07T12:00:00Z"},
                        is_open=0, current_order=7))
    for _ in range(20):  # still open in S0 -> excluded
        deals.append(mk("A", create, {0: create}, is_open=1, current_order=0))

    res = compute_conversion(deals, WEEKS)
    i = wk_index(create)
    cell = res["aggregate"]["weekly"][i]
    assert cell["advanced"] == 50
    assert cell["lost"] == 30
    assert cell["denom"] == 80          # the 20 open deals are NOT in the denom
    assert abs(cell["rate"] - 0.625) < 1e-9
    # cumulative through the end equals 0.625 too (only one populated week)
    assert abs(res["aggregate"]["cumulative"][-1]["rate"] - 0.625) < 1e-9


def test_advanced_then_lost_still_counts_advanced():
    """A deal that reached S2 then closed-lost cleared the S0->S1 gate."""
    create = "2026-05-04T12:00:00Z"
    deals = [
        mk("A", create, {0: create, 2: "2026-05-05T12:00:00Z", 7: "2026-05-20T12:00:00Z"},
           is_open=0, current_order=7),                 # advanced (reached S2) then lost
        mk("A", create, {0: create, 7: "2026-05-06T12:00:00Z"},
           is_open=0, current_order=7),                 # lost at S0
    ]
    cell = compute_conversion(deals, WEEKS)["aggregate"]["weekly"][wk_index(create)]
    assert cell["advanced"] == 1 and cell["lost"] == 1
    assert abs(cell["rate"] - 0.5) < 1e-9


def test_inverse_correlation_sanity():
    """When advance share goes up, the lost share goes down (same denominator)."""
    create = "2026-05-04T12:00:00Z"
    # week with high conversion: 9 advanced, 1 lost
    deals = []
    for _ in range(9):
        deals.append(mk("A", create, {0: create, 1: "2026-05-06T12:00:00Z"}, current_order=1))
    deals.append(mk("A", create, {0: create, 7: "2026-05-07T12:00:00Z"}, is_open=0, current_order=7))
    cell = compute_conversion(deals, WEEKS)["aggregate"]["weekly"][wk_index(create)]
    advanced_share = cell["advanced"] / cell["denom"]
    lost_share = cell["lost"] / cell["denom"]
    assert abs((advanced_share + lost_share) - 1.0) < 1e-9   # complementary by construction
    assert advanced_share == 0.9 and lost_share == 0.1


# --------------------------------------------------------------------------- #
# Speed to S1 — bucket by S1-transition week; pooling
# --------------------------------------------------------------------------- #
def test_speed_buckets_by_s1_week_and_means():
    # created week A, entered S1 in week B (10 days later)
    d1 = mk("A", "2026-05-04T00:00:00Z", {0: "2026-05-04T00:00:00Z", 1: "2026-05-14T00:00:00Z"}, current_order=1)
    # another deal entering S1 same week B, 6 days
    d2 = mk("A", "2026-05-08T00:00:00Z", {0: "2026-05-08T00:00:00Z", 1: "2026-05-14T00:00:00Z"}, current_order=1)
    res = compute_speed_to_s1([d1, d2], WEEKS)
    i = wk_index("2026-05-14T00:00:00Z")
    cell = res["aggregate"]["weekly"][i]
    assert cell["n"] == 2
    assert abs(cell["avg"] - 8.0) < 1e-9     # mean of 10 and 6


def test_speed_rolling_pools_underlying_days():
    # week1: one deal 10d; week2: one deal 4d. rolling4 at week2 = (10+4)/2 = 7
    w1 = mk("A", "2026-05-04T00:00:00Z", {0: "2026-05-04T00:00:00Z", 1: "2026-05-14T00:00:00Z"})
    w2 = mk("A", "2026-05-17T00:00:00Z", {0: "2026-05-17T00:00:00Z", 1: "2026-05-21T00:00:00Z"})
    res = compute_speed_to_s1([w1, w2], WEEKS)
    i2 = wk_index("2026-05-21T00:00:00Z")
    assert abs(res["aggregate"]["rolling4"][i2]["avg"] - 7.0) < 1e-9
    assert res["aggregate"]["rolling4"][i2]["n"] == 2


# --------------------------------------------------------------------------- #
# Pooling helpers
# --------------------------------------------------------------------------- #
def test_pooled_rate_series_pools_num_den_not_mean_of_rates():
    weeks = [(2026, 18), (2026, 19)]
    comp = {(2026, 18): {"advanced": 1, "lost": 9},    # 10%
            (2026, 19): {"advanced": 90, "lost": 10}}  # 90%
    s = pooled_rate_series(comp, weeks, window=4)
    # cumulative pools: (1+90)/(10+100) = 91/110, NOT mean(10%,90%)=50%
    assert abs(s["cumulative"][-1]["rate"] - (91 / 110)) < 1e-9


def test_pooled_mean_series_pools_sum_n():
    weeks = [(2026, 18), (2026, 19)]
    comp = {(2026, 18): {"sum": 100.0, "n": 10},   # avg 10
            (2026, 19): {"sum": 0.0, "n": 0}}
    s = pooled_mean_series(comp, weeks)
    assert s["weekly"][0]["avg"] == 10.0
    assert s["weekly"][1]["avg"] is None
    assert s["cumulative"][-1]["avg"] == 10.0


# --------------------------------------------------------------------------- #
# Scoping & attribution
# --------------------------------------------------------------------------- #
def test_in_quarter_excludes_pre_q2():
    pre = mk("A", "2026-03-30T00:00:00Z", {0: "2026-03-30T00:00:00Z"})
    inq = mk("A", "2026-05-04T00:00:00Z", {0: "2026-05-04T00:00:00Z"})
    assert not in_quarter(pre, QSTART, QEND)
    assert in_quarter(inq, QSTART, QEND)


def test_created_attributes_by_creator_and_unknown_bucket():
    deals = [
        mk("A", "2026-05-04T00:00:00Z", {0: "2026-05-04T00:00:00Z"}),
        mk("B", "2026-05-04T00:00:00Z", {0: "2026-05-04T00:00:00Z"}),
        mk(None, "2026-05-04T00:00:00Z", {0: "2026-05-04T00:00:00Z"}),  # created_by NULL
    ]
    res = compute_created(deals, WEEKS, QSTART, parse_ts("2026-06-30T00:00:00Z"))
    i = wk_index("2026-05-04T00:00:00Z")
    assert res["by_creator"]["A"][i] == 1
    assert res["by_creator"]["B"][i] == 1
    assert res["by_creator"]["unknown"][i] == 1
    assert res["aggregate"][i] == 3
    assert creator_key(deals[2]) == "unknown"
