"""Timezone-aware helpers (Africa/Casablanca by default)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import settings

_TZ = ZoneInfo(settings.timezone)


def get_tz() -> ZoneInfo:
    return _TZ


def now_local() -> datetime:
    return datetime.now(_TZ)


def ensure_local(dt: datetime) -> datetime:
    """Return `dt` as an aware datetime in the configured timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ)


def local_date(dt: datetime) -> date:
    return ensure_local(dt).date()


def combine_local(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=_TZ)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return ensure_local(dt).isoformat()


def day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min, tzinfo=_TZ)
    return start, start + timedelta(days=1)
