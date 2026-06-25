"""Small, dependency-free statistics: median/IQR plus distribution-free trend
detection (Mann-Kendall + Theil-Sen) used for the velocity "getting faster /
slower" call. Right-skewed, tiny-sample sales data => never trust the mean.
"""
import math
from typing import List, Optional, Sequence, Tuple

MIN_TREND_N = 8  # gate: need this many points before declaring a direction


def median(xs: Sequence[float]) -> Optional[float]:
    s = sorted(x for x in xs if x is not None)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def quantile(xs: Sequence[float], q: float) -> Optional[float]:
    s = sorted(x for x in xs if x is not None)
    n = len(s)
    if n == 0:
        return None
    if n == 1:
        return s[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def describe(xs: Sequence[float]) -> dict:
    vals = [x for x in xs if x is not None]
    return {
        "n": len(vals),
        "median": median(vals),
        "p25": quantile(vals, 0.25),
        "p75": quantile(vals, 0.75),
        "min": min(vals) if vals else None,
        "max": max(vals) if vals else None,
    }


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def mann_kendall(values: Sequence[float]) -> dict:
    """Trend test on a sequence (ordered by time). Returns S, tau, z, p."""
    x = [v for v in values if v is not None]
    n = len(x)
    if n < 3:
        return {"n": n, "S": 0, "tau": None, "z": None, "p": None}
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += (x[j] > x[i]) - (x[j] < x[i])
    var = n * (n - 1) * (2 * n + 5) / 18.0
    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    p = 2 * (1 - _norm_cdf(abs(z)))
    tau = s / (0.5 * n * (n - 1))
    return {"n": n, "S": s, "tau": tau, "z": z, "p": p}


def theil_sen(points: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Median pairwise slope of (t, y) points; robust to outliers."""
    slopes: List[float] = []
    pts = [(t, y) for t, y in points if t is not None and y is not None]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dt = pts[j][0] - pts[i][0]
            if dt == 0:
                continue
            slopes.append((pts[j][1] - pts[i][1]) / dt)
    return median(slopes)


def trend(points: Sequence[Tuple[float, float]], min_n: int = MIN_TREND_N,
          metric: str = "duration") -> dict:
    """Combine MK + Theil-Sen on (week_index, value) points.

    metric='duration': direction 'faster' (slope<0) / 'slower' (slope>0).
    metric='count':    direction 'rising' (slope>0) / 'falling' (slope<0).
    Either way 'flat' when not significant, 'insufficient_data' when n<min_n.
    """
    pts = [(t, y) for t, y in points if t is not None and y is not None]
    n = len(pts)
    if n < min_n:
        return {"n": n, "direction": "insufficient_data", "slope_per_week": None,
                "tau": None, "p": None, "significant": False}
    mk = mann_kendall([y for _, y in sorted(pts)])
    slope = theil_sen(pts)
    significant = mk["p"] is not None and mk["p"] < 0.10
    if slope is None or abs(slope) < 1e-9 or not significant:
        direction = "flat"
    elif metric == "count":
        direction = "rising" if slope > 0 else "falling"
    else:
        direction = "faster" if slope < 0 else "slower"  # durations shrinking = faster
    return {"n": n, "direction": direction, "slope_per_week": slope,
            "tau": mk["tau"], "p": mk["p"], "significant": significant}
