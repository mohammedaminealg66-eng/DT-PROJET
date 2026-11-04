"""Session windows (the four fixed daily time slots)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..models import Session as SessionModel
from ..timeutil import combine_local, ensure_local, get_tz, iso, local_date


@dataclass(frozen=True)
class SessionWindow:
    id: int
    session_number: int
    start_time: time
    end_time: time

    def start_at(self, d: date) -> datetime:
        return combine_local(d, self.start_time)

    def end_at(self, d: date) -> datetime:
        return combine_local(d, self.end_time)

    def contains(self, moment: datetime) -> bool:
        local = ensure_local(moment)
        return self.start_time <= local.time() < self.end_time

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "session_number": self.session_number,
            "start_time": self.start_time.isoformat(timespec="minutes"),
            "end_time": self.end_time.isoformat(timespec="minutes"),
        }


class SessionManager:
    """Read-only view of the four recurring daily sessions."""

    def __init__(self, windows: list[SessionWindow]) -> None:
        self.windows = sorted(windows, key=lambda w: w.session_number)
        self.by_id = {w.id: w for w in self.windows}
        self.by_number = {w.session_number: w for w in self.windows}

    @classmethod
    def load(cls, db: OrmSession) -> "SessionManager":
        rows = db.scalars(
            select(SessionModel).order_by(SessionModel.session_number)
        ).all()
        return cls(
            [
                SessionWindow(
                    id=row.id,
                    session_number=row.session_number,
                    start_time=row.start_time,
                    end_time=row.end_time,
                )
                for row in rows
            ]
        )

    def window_by_id(self, session_id: int) -> Optional[SessionWindow]:
        return self.by_id.get(session_id)

    def window_by_number(self, number: int) -> Optional[SessionWindow]:
        return self.by_number.get(number)

    def window_at(self, moment: datetime) -> Optional[SessionWindow]:
        for window in self.windows:
            if window.contains(moment):
                return window
        return None

    def context(self, moment: datetime) -> dict:
        """Snapshot of the session situation at `moment`."""
        local = ensure_local(moment)
        current = self.window_at(local)

        if self.windows:
            day_start = self.windows[0].start_time
            day_end = self.windows[-1].end_time
        else:  # pragma: no cover - guarded by OccupancyManager
            day_start = day_end = None

        before_hours = bool(day_start and local.time() < day_start)
        after_hours = bool(day_end and local.time() >= day_end)
        is_break = bool(
            current is None
            and day_start is not None
            and day_end is not None
            and day_start <= local.time() < day_end
        )

        next_window = next(
            (w for w in self.windows if w.start_time > local.time()), None
        )
        if next_window is not None:
            next_start = next_window.start_at(local.date())
        elif after_hours and self.windows:
            next_start = self.windows[0].start_at(local.date() + timedelta(days=1))
        else:
            next_start = None

        return {
            "session": current.as_dict() if current else None,
            "session_date": local.date().isoformat(),
            "local_time": local.isoformat(),
            "timezone": get_tz().key,
            "is_break": is_break,
            "before_hours": before_hours,
            "after_hours": after_hours,
            "next_session_start": iso(next_start) if next_start else None,
        }
