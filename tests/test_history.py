"""Unit tests for the weekly metric-history snapshot logic (no DB / no network)."""
import sqlite3
from datetime import timedelta

from app.history import (HISTORY_SCHEMA, VELOCITY_PERIODS, build_rows,
                         last_complete_week, target_moment)
from app.metrics.windows import iso_week_key, parse_ts, week_start_date


def test_build_rows_shape_and_values():
    vel = {
        "full": {"median": 15.9, "mean": 18.8, "n": 26},
        "first_third": {"median": 19.9, "mean": 22.1, "n": 9},
        "second_third": {"median": 15.1, "mean": 22.6, "n": 9},
        "last_third": {"median": 9.4, "mean": 10.7, "n": 8},
    }
    fun = {"gates": [
        {"key": "S0_S1", "rate": 0.31, "advanced": 71, "lost": 155, "denom": 226},
        {"key": "S1_S2", "rate": 0.61, "advanced": 41, "lost": 26, "denom": 67},
    ], "full_funnel": 0.026}
    vrows, frows = build_rows(vel, fun, "2026-04-01", 2026, 20, "T")
    assert len(vrows) == 4 and len(frows) == 2
    full = next(r for r in vrows if r[3] == "full")
    assert full[4] == 15.9 and full[5] == 18.8 and full[6] == 26
    s1s2 = next(r for r in frows if r[3] == "S1_S2")
    assert s1s2[4] == 0.61 and s1s2[5] == 41 and s1s2[7] == 67  # rate, advanced, denom


def test_last_complete_week_matches_definition():
    now = parse_ts("2026-05-13T10:00:00Z")
    cy, cw = iso_week_key(now)
    prior_end = week_start_date(cy, cw) - timedelta(seconds=1)  # Sun 23:59:59 last week
    assert last_complete_week(now) == iso_week_key(prior_end)
    assert last_complete_week(now) != (cy, cw)


def test_target_moment_is_weekday_hour_of_current_week():
    now = parse_ts("2026-05-13T10:00:00Z")
    cy, cw = iso_week_key(now)
    assert target_moment(now, 0, 6) == week_start_date(cy, cw) + timedelta(days=0, hours=6)


def test_snapshot_idempotent_one_row_per_week_per_period(tmp_path):
    con = sqlite3.connect(tmp_path / "h.db")
    con.executescript(HISTORY_SCHEMA)
    rows = [("2026-04-01", 2026, 20, p, 1.0, 2.0, 3, "T") for p in VELOCITY_PERIODS]
    for _ in range(2):  # re-running the same week must overwrite, not accumulate
        con.executemany(
            "INSERT OR REPLACE INTO velocity_history(quarter_start,iso_year,iso_week,period,median,mean,n,taken_at) VALUES(?,?,?,?,?,?,?,?)",
            rows)
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM velocity_history").fetchone()[0] == 4
    con.close()
