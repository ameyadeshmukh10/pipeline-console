"""Unit tests for the statistics helpers."""
from app.metrics.stats import describe, mann_kendall, median, quantile, theil_sen, trend


def test_median_quantile():
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert median([]) is None
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    d = describe([1, 2, 3, 4, 100])
    assert d["n"] == 5 and d["median"] == 3 and d["max"] == 100


def test_mann_kendall_monotonic():
    inc = mann_kendall([1, 2, 3, 4, 5, 6, 7, 8])
    assert inc["S"] > 0 and inc["tau"] == 1.0 and inc["p"] < 0.05
    dec = mann_kendall([8, 7, 6, 5, 4, 3, 2, 1])
    assert dec["S"] < 0 and dec["tau"] == -1.0


def test_theil_sen_slope():
    # y = 2x
    assert theil_sen([(0, 0), (1, 2), (2, 4), (3, 6)]) == 2.0


def test_trend_duration_faster():
    pts = [(i, 30 - 2 * i) for i in range(10)]  # durations shrinking
    t = trend(pts, metric="duration")
    assert t["direction"] == "faster" and t["slope_per_week"] < 0


def test_trend_count_rising():
    pts = [(i, 5 + i) for i in range(10)]  # volume climbing
    t = trend(pts, metric="count")
    assert t["direction"] == "rising" and t["slope_per_week"] > 0


def test_trend_insufficient():
    assert trend([(0, 1), (1, 2)], min_n=8)["direction"] == "insufficient_data"
