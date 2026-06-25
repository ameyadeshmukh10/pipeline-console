"""Time + ISO-week helpers. All storage is UTC; weekly buckets are computed in
the configured business timezone (default America/New_York) so an event near
Sunday/Monday midnight lands in a consistent ISO week everywhere.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

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


def quarter_start_dt() -> datetime:
    """Configured Q2 start, midnight business tz, as UTC."""
    y, m, d = (int(x) for x in settings.QUARTER_START.split("-"))
    local = datetime(y, m, d, tzinfo=_BIZ_TZ)
    return local.astimezone(timezone.utc)


def quarter_end_dt() -> datetime:
    """End of the quarter (3 months after start, minus 1 second), as UTC."""
    y, m, d = (int(x) for x in settings.QUARTER_START.split("-"))
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
