"""Time + ISO-week helpers. All storage is UTC; weekly buckets are computed in
the configured business timezone (default America/New_York) so an event near
Sunday/Monday midnight lands in a consistent ISO week everywhere.

Also owns the APPLICATION-WIDE ANALYSIS WINDOW: every report scopes to
[window_start_dt(), window_end_dt()]. The window is configured from the
DB-backed settings (window_start / window_end / window_continuous) via
``configure_window`` — called at startup and on every settings save. When a
window is continuous it has no end date: window_end_dt() is "now". Unconfigured
(e.g. plain unit tests) it falls back to the QUARTER_START env default with the
legacy derived end (start + 3 months − 1s).
"""
import re
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from ..config import settings

try:
    from zoneinfo import ZoneInfo
    _BIZ_TZ = ZoneInfo(settings.TIMEZONE)
except Exception:  # pragma: no cover - fallback if tzdata missing
    _BIZ_TZ = timezone.utc

_ISO_CLEAN = re.compile(r"(\.\d{1,6})\d*")  # trim fractional seconds to <=6 digits


def biz_tz():
    return _BIZ_TZ


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse a HubSpot timestamp (ISO-8601 string or epoch-millis) to aware UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():  # epoch millis
        try:
            return datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    s = s.replace("Z", "+00:00")
    s = _ISO_CLEAN.sub(lambda m: m.group(1), s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso_utc(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat()


def iso_week_key(dt_utc: Optional[datetime]) -> Tuple[Optional[int], Optional[int]]:
    """(iso_year, iso_week) for a UTC datetime, computed in business tz."""
    if dt_utc is None:
        return (None, None)
    local = dt_utc.astimezone(_BIZ_TZ)
    iso = local.isocalendar()
    return (iso[0], iso[1])


def week_label(iso_year: int, iso_week: int) -> str:
    return f"{iso_year}-W{iso_week:02d}"


def week_start_date(iso_year: int, iso_week: int) -> datetime:
    """Monday 00:00 (business tz) of the given ISO week, as UTC datetime."""
    local = datetime.fromisocalendar(iso_year, iso_week, 1).replace(tzinfo=_BIZ_TZ)
    return local.astimezone(timezone.utc)


def week_end_date(iso_year: int, iso_week: int) -> datetime:
    """Sunday 23:59:59 (business tz) of the given ISO week, as UTC datetime."""
    local = datetime.fromisocalendar(iso_year, iso_week, 7).replace(
        hour=23, minute=59, second=59, tzinfo=_BIZ_TZ
    )
    return local.astimezone(timezone.utc)


# --- analysis window --------------------------------------------------------
_window: Dict[str, Optional[str]] = {"start": None, "end": None, "continuous": None}


def _parse_date(value, field: str) -> Tuple[int, int, int]:
    try:
        y, m, d = (int(x) for x in str(value).strip().split("-"))
        date(y, m, d)  # range-validate
        return y, m, d
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a YYYY-MM-DD date, got {value!r}")


def configure_window(start: Optional[str], end: Optional[str],
                     continuous: Optional[bool]) -> None:
    """Set the in-process analysis window (values come from the settings DB).

    ``start``/``end`` are YYYY-MM-DD strings or None; ``continuous`` means the
    window has no end date. Pass all-None to reset to the env-default quarter.
    Raises ValueError on malformed dates or end < start (nothing is changed).
    """
    if start is not None:
        _parse_date(start, "window_start")
    if end is not None and str(end).strip() != "":
        _parse_date(end, "window_end")
        if start is not None and not continuous:
            if date(*_parse_date(end, "window_end")) < date(*_parse_date(start, "window_start")):
                raise ValueError("window_end must be on or after window_start")
    else:
        end = None
    _window["start"] = start
    _window["end"] = end
    _window["continuous"] = continuous


def window_is_continuous() -> bool:
    """True when the window has no end date (runs through 'now')."""
    return bool(_window["continuous"])


def window_start_dt() -> datetime:
    """Window start, midnight business tz, as UTC."""
    y, m, d = _parse_date(_window["start"] or settings.QUARTER_START, "window_start")
    local = datetime(y, m, d, tzinfo=_BIZ_TZ)
    return local.astimezone(timezone.utc)


def window_end_dt() -> datetime:
    """Window end as UTC: 'now' when continuous, else the configured end date
    (inclusive — 23:59:59 business tz). Unconfigured legacy fallback: 3 months
    after the start, minus 1 second (a calendar quarter)."""
    if window_is_continuous():
        return now_utc()
    if _window["end"]:
        y, m, d = _parse_date(_window["end"], "window_end")
        local = datetime(y, m, d, 23, 59, 59, tzinfo=_BIZ_TZ)
        return local.astimezone(timezone.utc)
    y, m, _d = _parse_date(_window["start"] or settings.QUARTER_START, "window_start")
    em, ey = m + 3, y
    if em > 12:
        em -= 12
        ey += 1
    first_next = datetime(ey, em, 1, tzinfo=_BIZ_TZ)
    return (first_next - timedelta(seconds=1)).astimezone(timezone.utc)


def iso_weeks_in_range(start: datetime, end: datetime) -> List[Tuple[int, int]]:
    """Ordered list of (iso_year, iso_week) covering [start, end] inclusive."""
    weeks: List[Tuple[int, int]] = []
    seen = set()
    cur = start.astimezone(_BIZ_TZ)
    end_local = end.astimezone(_BIZ_TZ)
    while cur <= end_local:
        iso = cur.isocalendar()
        key = (iso[0], iso[1])
        if key not in seen:
            seen.add(key)
            weeks.append(key)
        cur += timedelta(days=1)
    return weeks


def days_between(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


def is_mature(iso_year: int, iso_week: int, horizon_days: int, ref: Optional[datetime] = None) -> bool:
    """A cohort week is mature only if the horizon has fully elapsed since week end."""
    ref = ref or now_utc()
    return (ref - week_end_date(iso_year, iso_week)).total_seconds() >= horizon_days * 86400.0
