"""Unit tests for the configurable application-wide analysis window.

The window is process-global (configured from the settings DB at runtime);
every test restores the unconfigured state so the other test modules keep
seeing the legacy env-default quarter.
"""
from datetime import timedelta

import pytest

from app.config import settings
from app.metrics.windows import (biz_tz, configure_window, now_utc,
                                 window_end_dt, window_is_continuous,
                                 window_start_dt)


@pytest.fixture(autouse=True)
def _reset_window():
    yield
    configure_window(None, None, None)


def _local(dt):
    return dt.astimezone(biz_tz())


def test_unconfigured_falls_back_to_env_quarter():
    y, m, d = (int(x) for x in settings.QUARTER_START.split("-"))
    start = _local(window_start_dt())
    assert (start.year, start.month, start.day, start.hour) == (y, m, d, 0)
    # Legacy derived end: 1 second before the first day of start-month + 3.
    em, ey = (m + 3, y) if m + 3 <= 12 else (m + 3 - 12, y + 1)
    after_end = _local(window_end_dt()) + timedelta(seconds=1)
    assert (after_end.year, after_end.month, after_end.day, after_end.hour,
            after_end.minute, after_end.second) == (ey, em, 1, 0, 0, 0)
    assert not window_is_continuous()


def test_explicit_end_is_inclusive_end_of_day():
    configure_window("2026-04-01", "2026-09-30", False)
    end = _local(window_end_dt())
    assert (end.year, end.month, end.day) == (2026, 9, 30)
    assert (end.hour, end.minute, end.second) == (23, 59, 59)
    assert not window_is_continuous()


def test_continuous_window_has_no_end_date():
    configure_window("2026-01-15", None, True)
    assert window_is_continuous()
    assert abs((window_end_dt() - now_utc()).total_seconds()) < 5
    start = _local(window_start_dt())
    assert (start.year, start.month, start.day) == (2026, 1, 15)


def test_continuous_overrides_a_stored_end_date():
    # The end date is kept in settings (so untoggling restores it) but ignored.
    configure_window("2026-04-01", "2026-05-01", True)
    assert window_is_continuous()
    assert abs((window_end_dt() - now_utc()).total_seconds()) < 5


def test_end_before_start_rejected():
    with pytest.raises(ValueError):
        configure_window("2026-04-01", "2026-03-01", False)
    # Nothing was applied: still the unconfigured fallback.
    assert not window_is_continuous()


def test_malformed_dates_rejected():
    with pytest.raises(ValueError):
        configure_window("not-a-date", None, True)
    with pytest.raises(ValueError):
        configure_window("2026-04-01", "2026-13-40", False)
