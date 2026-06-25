"""Pure smoothing helpers shared by the Pre-Pipeline speed & conversion charts.

Three views of the same weekly data, all computed by POOLING the underlying
components (never by averaging already-averaged weekly numbers):

  - weekly      : the isolated week only
  - rolling4    : the trailing `window` weeks pooled together
  - cumulative  : every week from the start through this one, pooled

For means we pool (sum_of_values, n); for rates we pool (advanced, lost). This
is the statistically correct way to "manufacture sample size" — a 4-week
rolling rate is Σadvanced / Σ(advanced+lost), not the mean of four weekly rates.
"""
from typing import Dict, List, Optional, Tuple

from .stats import median, quantile, theil_sen, trend

Week = Tuple[int, int]


def pooled_median_series(values_by_week: Dict[Week, List[float]], weeks: List[Week],
                         window: int = 4) -> Dict[str, List[Dict]]:
    """Median-of-pooled-raw-values series. Medians CANNOT be pooled from
    sub-medians, so each window concatenates the underlying values first, then
    takes the median. Cell = {avg (=median), n, p25, p75} (key 'avg' so the
    frontend renders mean & median cells identically)."""
    weekly: List[Dict] = []
    rolling: List[Dict] = []
    cumulative: List[Dict] = []
    cum: List[float] = []
    for i, wk in enumerate(weeks):
        wv = values_by_week.get(wk) or []
        weekly.append(_median_cell(wv))
        rv: List[float] = []
        for j in range(max(0, i - window + 1), i + 1):
            rv.extend(values_by_week.get(weeks[j]) or [])
        rolling.append(_median_cell(rv))
        cum.extend(wv)
        cumulative.append(_median_cell(cum))
    return {"weekly": weekly, "rolling4": rolling, "cumulative": cumulative}


def _median_cell(vals: List[float]) -> Dict:
    return {"avg": median(vals), "n": len(vals),
            "p25": quantile(vals, 0.25), "p75": quantile(vals, 0.75)}


def pooled_count_series(counts: List[int], weeks: List[Week], window: int = 4,
                        complete: Optional[List[bool]] = None) -> Dict[str, List]:
    """Three views of a weekly COUNT series, aligned to `weeks`:
      weekly     = raw counts
      rolling4   = trailing mean of the last `window` weekly counts (signal vs noise)
      cumulative = running total
      pace       = constant-average-rate reference for the cumulative view
                   (avg_rate*(i+1), avg_rate over COMPLETE weeks only)
    """
    n = len(weeks)
    weekly = [int(c or 0) for c in counts]
    if complete is None:
        complete = [True] * n
    rolling: List[Optional[float]] = []
    cumulative: List[int] = []
    run = 0
    for i in range(n):
        win = weekly[max(0, i - window + 1):i + 1]
        rolling.append(sum(win) / len(win) if win else None)
        run += weekly[i]
        cumulative.append(run)
    comp = [weekly[i] for i in range(n) if complete[i]]
    avg_rate = (sum(comp) / len(comp)) if comp else 0.0
    pace = [avg_rate * (i + 1) for i in range(n)]
    return {"weekly": weekly, "rolling4": rolling, "cumulative": cumulative, "pace": pace}


def fit_line(counts: List[int], weeks: List[Week],
             complete: List[bool]) -> Optional[Dict]:
    """Robust (Theil-Sen) straight-line fit over COMPLETE weeks only, returned as
    a y-value per week so it can be drawn across the whole chart. Excluding the
    partial (current) week stops it from faking a downturn."""
    pts = [(float(i), float(counts[i])) for i in range(len(weeks))
           if complete[i] and counts[i] is not None]
    if len(pts) < 2:
        return None
    direction = trend(pts, min_n=4, metric="count")["direction"]
    # Tie the drawn line to the significance verdict: a horizontal baseline at
    # the median when the net trend is flat/insufficient (so it matches the
    # "flat" tag and never implies a slope through hump-shaped data); a robust
    # Theil-Sen slope only when the trend is statistically real.
    if direction in ("flat", "insufficient_data"):
        level = median([y for _, y in pts]) or 0.0
        return {"slope": 0.0, "intercept": level,
                "y": [level] * len(weeks), "direction": direction}
    slope = theil_sen(pts) or 0.0
    intercept = median([y - slope * t for t, y in pts]) or 0.0
    y = [slope * i + intercept for i in range(len(weeks))]
    return {"slope": slope, "intercept": intercept, "y": y, "direction": direction}


def pooled_mean_series(comp: Dict[Week, Dict[str, float]], weeks: List[Week],
                       window: int = 4) -> Dict[str, List[Dict]]:
    """comp[week] = {"sum": float, "n": int}. Returns weekly/rolling4/cumulative
    lists aligned to `weeks`; each cell = {"avg": float|None, "n": int}."""
    weekly: List[Dict] = []
    rolling: List[Dict] = []
    cumulative: List[Dict] = []
    csum = 0.0
    cn = 0
    for i, wk in enumerate(weeks):
        c = comp.get(wk) or {"sum": 0.0, "n": 0}
        n = int(c["n"])
        weekly.append({"avg": (c["sum"] / n) if n else None, "n": n})

        rsum = 0.0
        rn = 0
        for j in range(max(0, i - window + 1), i + 1):
            cj = comp.get(weeks[j]) or {"sum": 0.0, "n": 0}
            rsum += cj["sum"]
            rn += int(cj["n"])
        rolling.append({"avg": (rsum / rn) if rn else None, "n": rn})

        csum += c["sum"]
        cn += n
        cumulative.append({"avg": (csum / cn) if cn else None, "n": cn})
    return {"weekly": weekly, "rolling4": rolling, "cumulative": cumulative}


def pooled_rate_series(comp: Dict[Week, Dict[str, int]], weeks: List[Week],
                       window: int = 4) -> Dict[str, List[Dict]]:
    """comp[week] = {"advanced": int, "lost": int}. Returns weekly/rolling4/
    cumulative lists aligned to `weeks`; each cell =
    {"rate": float|None (0-1), "advanced": int, "lost": int, "denom": int}.
    rate = advanced / (advanced + lost)."""
    weekly: List[Dict] = []
    rolling: List[Dict] = []
    cumulative: List[Dict] = []
    cadv = 0
    clost = 0
    for i, wk in enumerate(weeks):
        c = comp.get(wk) or {"advanced": 0, "lost": 0}
        adv, lost = int(c["advanced"]), int(c["lost"])
        weekly.append(_rate_cell(adv, lost))

        radv = rlost = 0
        for j in range(max(0, i - window + 1), i + 1):
            cj = comp.get(weeks[j]) or {"advanced": 0, "lost": 0}
            radv += int(cj["advanced"])
            rlost += int(cj["lost"])
        rolling.append(_rate_cell(radv, rlost))

        cadv += adv
        clost += lost
        cumulative.append(_rate_cell(cadv, clost))
    return {"weekly": weekly, "rolling4": rolling, "cumulative": cumulative}


def _rate_cell(advanced: int, lost: int) -> Dict:
    denom = advanced + lost
    return {"rate": (advanced / denom) if denom else None,
            "advanced": advanced, "lost": lost, "denom": denom}
