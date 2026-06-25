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
from typing import Dict, List, Tuple

Week = Tuple[int, int]


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
