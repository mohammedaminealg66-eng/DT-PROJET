"""Occupancy state machine.

Responsibilities
----------------
* Hysteresis: a seat becomes OCCUPIED after N seconds of continuous
  detection and EMPTY after M seconds of continuous absence.
* One database record per continuous occupancy period (never merged).
* Session boundaries: close records at the exact session end, open new
  records at the exact next session start for seats that are still
  physically present.
* Restart recovery: reload open records without duplicating them.

The manager is synchronous and thread-safe (one lock); callers may invoke
it from FastAPI's threadpool or via ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ..models import OccupancyRecord, Seat
from ..timeutil import ensure_local, iso, local_date, now_local
from .session_manager import SessionManager, SessionWindow

log = logging.getLogger(__name__)

EMPTY = "EMPTY"
OCCUPIED = "OCCUPIED"


@dataclass
class ChangeResult:
    """Outcome of an observation / boundary tick."""

    changed: bool = False          # a database record was created or closed
    presence_changed: bool = False  # a seat state changed (even without a record)
    events: list[dict] = field(default_factory=list)
    unknown_codes: list[str] = field(default_factory=list)

    def merge(self, other: "ChangeResult") -> "ChangeResult":
        self.changed = self.changed or other.changed
        self.presence_changed = self.presence_changed or other.presence_changed
        self.events.extend(other.events)
        if other.unknown_codes:
            self.unknown_codes.extend(other.unknown_codes)
        return self


@dataclass
class SeatRuntime:
    seat_id: int
    seat_code: str
    state: str = EMPTY
    # For EMPTY: start of the continuous detection.
    # For OCCUPIED: start of the continuous absence.
    pending_since: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    open_record_id: Optional[int] = None
    open_session_id: Optional[int] = None
    open_from: Optional[datetime] = None

    def as_dict(self) -> dict:
        return {
            "seat_id": self.seat_id,
            "seat_code": self.seat_code,
            "state": self.state,
            "present": self.state == OCCUPIED,
            "occupied": self.open_record_id is not None,
            "pending_since": iso(self.pending_since),
            "last_seen": iso(self.last_seen),
            "open_record_id": self.open_record_id,
            "open_session_id": self.open_session_id,
            "occupied_from": iso(self.open_from),
        }


class OccupancyManager:
    def __init__(
        self,
        session_factory: sessionmaker,
        session_manager: SessionManager,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        occupied_confirm_seconds: float = 2.0,
        empty_confirm_seconds: float = 3.0,
        presence_grace_seconds: float = 15.0,
    ) -> None:
        if not session_manager.windows:
            raise RuntimeError(
                "No sessions found. Run the SQL seed (backend/sql/002_seed.sql) first."
            )

        self.session_factory = session_factory
        self.session_manager = session_manager
        self.clock = clock or now_local
        self.occupied_confirm_seconds = float(occupied_confirm_seconds)
        self.empty_confirm_seconds = float(empty_confirm_seconds)
        self.presence_grace_seconds = float(presence_grace_seconds)

        self._lock = threading.RLock()
        self.seats: dict[str, SeatRuntime] = {}
        self.seats_by_id: dict[int, SeatRuntime] = {}

        self._load_seats()

        now = self._now()
        current = self.session_manager.window_at(now)
        self._last_key = (current.id if current else None, local_date(now))
        self.recovered_seats = self._recover(now)
        if self.recovered_seats:
            log.info(
                "Recovered %d open occupancy record(s): %s",
                len(self.recovered_seats),
                ", ".join(self.recovered_seats),
            )

    # ---------------------------------------------------------------- setup
    def _load_seats(self) -> None:
        with self.session_factory() as db:
            rows = db.scalars(
                select(Seat).order_by(Seat.column_number, Seat.row_number)
            ).all()
        for row in rows:
            runtime = SeatRuntime(seat_id=row.id, seat_code=row.seat_code)
            self.seats[row.seat_code] = runtime
            self.seats_by_id[row.id] = runtime

    def _recover(self, now: datetime) -> list[str]:
        """Reload open records after a restart.

        * Open record for the CURRENT session/date -> seat stays OCCUPIED.
        * Any other open record (previous session, previous day) -> close it
          at its scheduled session end. We do not invent occupancy for the
          current session because we cannot know whether the person is still
          there; the next detection will open a fresh record.
        """
        recovered: list[str] = []
        current = self.session_manager.window_at(now)
        today = local_date(now)

        with self.session_factory() as db, db.begin():
            open_records = db.scalars(
                select(OccupancyRecord).where(
                    OccupancyRecord.occupied_until.is_(None)
                )
            ).all()

            for record in open_records:
                runtime = self.seats_by_id.get(record.seat_id)
                if runtime is None:
                    continue
                window = self.session_manager.window_by_id(record.session_id)

                if (
                    current is not None
                    and window is not None
                    and record.session_id == current.id
                    and record.session_date == today
                ):
                    runtime.state = OCCUPIED
                    runtime.open_record_id = record.id
                    runtime.open_session_id = record.session_id
                    runtime.open_from = ensure_local(record.occupied_from)
                    runtime.last_seen = now
                    recovered.append(runtime.seat_code)
                    continue

                end = window.end_at(record.session_date) if window else now
                end = ensure_local(end)
                if end > now:
                    end = now
                start = ensure_local(record.occupied_from)
                if end < start:
                    end = start
                record.occupied_until = end
                log.info(
                    "Recovery: closed stale record %s (%s) at %s",
                    record.id,
                    runtime.seat_code,
                    end.isoformat(),
                )

        return recovered

    # ------------------------------------------------------------- helpers
    def _now(self) -> datetime:
        return ensure_local(self.clock())

    @staticmethod
    def _norm(moment: Optional[datetime]) -> datetime:
        return ensure_local(moment) if moment is not None else ensure_local(now_local())

    def _is_present(self, runtime: SeatRuntime, now: datetime) -> bool:
        """A seat may be carried across a boundary only if it is confirmed
        occupied, has no pending absence, and was seen recently."""
        if runtime.state != OCCUPIED or runtime.pending_since is not None:
            return False
        if runtime.last_seen is None:
            return False
        return (now - runtime.last_seen).total_seconds() <= self.presence_grace_seconds

    def _event(
        self,
        action: str,
        runtime: Optional[SeatRuntime],
        window: Optional[SessionWindow],
        at: datetime,
        *,
        reason: Optional[str] = None,
        record_id: Optional[int] = None,
        until: Optional[datetime] = None,
    ) -> dict:
        return {
            "type": "seat_event",
            "action": action,
            "seat": runtime.seat_code if runtime else None,
            "seat_id": runtime.seat_id if runtime else None,
            "session_id": window.id if window else None,
            "session_number": window.session_number if window else None,
            "session_date": local_date(at).isoformat(),
            "at": iso(at),
            "occupied_until": iso(until),
            "record_id": record_id,
            "reason": reason,
        }

    # ----------------------------------------------------------- hysteresis
    def observe(
        self,
        seat_code: str,
        detected: bool,
        at: Optional[datetime] = None,
    ) -> ChangeResult:
        """Feed one detection sample for one seat."""
        moment = self._now() if at is None else ensure_local(at)
        with self._lock:
            runtime = self.seats.get(seat_code)
            if runtime is None:
                return ChangeResult(unknown_codes=[seat_code])
            return self._apply_observation(runtime, bool(detected), moment)

    def apply_snapshot(
        self,
        detected_seats: Iterable[str],
        at: Optional[datetime] = None,
    ) -> ChangeResult:
        """Apply a FULL snapshot: every known seat not in the list is absent."""
        moment = self._now() if at is None else ensure_local(at)
        codes = set(detected_seats or [])
        result = ChangeResult()
        with self._lock:
            for code, runtime in self.seats.items():
                try:
                    result.merge(
                        self._apply_observation(runtime, code in codes, moment)
                    )
                except Exception:  # keep one bad seat from killing the snapshot
                    log.exception("Failed to apply observation for %s", code)
            for code in codes:
                if code not in self.seats:
                    result.unknown_codes.append(code)
        return result

    def _apply_observation(
        self, runtime: SeatRuntime, detected: bool, at: datetime
    ) -> ChangeResult:
        result = ChangeResult()

        if detected:
            if runtime.last_seen is None or at > runtime.last_seen:
                runtime.last_seen = at
            if runtime.state == OCCUPIED:
                runtime.pending_since = None
                return result
            if runtime.pending_since is None:
                runtime.pending_since = at
            elapsed = (at - runtime.pending_since).total_seconds()
            if elapsed + 1e-9 >= self.occupied_confirm_seconds:
                detected_since = runtime.pending_since
                runtime.pending_since = None
                result.merge(self._open_record(runtime, detected_since, at))
            return result

        # not detected
        if runtime.state == EMPTY:
            runtime.pending_since = None
            return result
        if runtime.pending_since is None:
            runtime.pending_since = at
        elapsed = (at - runtime.pending_since).total_seconds()
        if elapsed + 1e-9 >= self.empty_confirm_seconds:
            absent_since = runtime.pending_since
            runtime.pending_since = None
            result.merge(self._close_record(runtime, absent_since, at))
        return result

    def _open_record(
        self, runtime: SeatRuntime, detected_since: datetime, at: datetime
    ) -> ChangeResult:
        result = ChangeResult()
        runtime.state = OCCUPIED
        result.presence_changed = True

        window = self.session_manager.window_at(at)
        if window is None:
            # Physically present during a break / outside sessions: the seat
            # is tracked in memory but NO database record is created.
            return result

        today = local_date(at)
        start = max(ensure_local(detected_since), window.start_at(today))
        if start >= window.end_at(today):
            return result

        if runtime.open_record_id is not None and runtime.open_session_id == window.id:
            return result

        record_id: Optional[int] = None
        try:
            with self.session_factory() as db, db.begin():
                existing = db.scalar(
                    select(OccupancyRecord).where(
                        OccupancyRecord.seat_id == runtime.seat_id,
                        OccupancyRecord.session_id == window.id,
                        OccupancyRecord.session_date == today,
                        OccupancyRecord.occupied_until.is_(None),
                    )
                )
                if existing is not None:
                    record = existing
                else:
                    record = OccupancyRecord(
                        seat_id=runtime.seat_id,
                        session_id=window.id,
                        session_date=today,
                        occupied_from=start,
                        occupied_until=None,
                    )
                    db.add(record)
                    db.flush()
                record_id = record.id
                runtime.open_record_id = record.id
                runtime.open_session_id = window.id
                runtime.open_from = ensure_local(record.occupied_from)
        except IntegrityError:
            # Another writer won the race; adopt the existing open record.
            log.warning("Open-record race for %s; reusing existing row", runtime.seat_code)
            with self.session_factory() as db:
                existing = db.scalar(
                    select(OccupancyRecord).where(
                        OccupancyRecord.seat_id == runtime.seat_id,
                        OccupancyRecord.session_id == window.id,
                        OccupancyRecord.session_date == today,
                        OccupancyRecord.occupied_until.is_(None),
                    )
                )
                if existing is None:
                    return result
                record_id = existing.id
                runtime.open_record_id = existing.id
                runtime.open_session_id = window.id
                runtime.open_from = ensure_local(existing.occupied_from)

        result.changed = True
        result.events.append(
            self._event("enter", runtime, window, start, record_id=record_id)
        )
        return result

    def _close_record(
        self, runtime: SeatRuntime, absent_since: datetime, at: datetime
    ) -> ChangeResult:
        result = ChangeResult()
        runtime.state = EMPTY
        result.presence_changed = True

        record_id = runtime.open_record_id
        session_id = runtime.open_session_id
        runtime.open_record_id = None
        runtime.open_session_id = None
        runtime.open_from = None

        if record_id is None:
            return result

        closed_until: Optional[datetime] = None
        with self.session_factory() as db, db.begin():
            record = db.get(OccupancyRecord, record_id)
            if record is None or record.occupied_until is not None:
                return result
            until = max(ensure_local(absent_since), ensure_local(record.occupied_from))
            if until > at:
                until = at
            record.occupied_until = until
            session_id = record.session_id
            closed_until = ensure_local(until)

        result.changed = True
        result.events.append(
            self._event(
                "leave",
                runtime,
                self.session_manager.window_by_id(session_id) if session_id else None,
                closed_until or at,
                record_id=record_id,
                until=closed_until,
            )
        )
        return result

    # ------------------------------------------------------------- sessions
    def tick(self, now: Optional[datetime] = None) -> ChangeResult:
        """Detect a session boundary and move records across it.

        Called periodically (and with explicit timestamps in tests).
        """
        moment = self._now() if now is None else ensure_local(now)
        with self._lock:
            current = self.session_manager.window_at(moment)
            key = (current.id if current else None, local_date(moment))
            if key == self._last_key:
                return ChangeResult()
            result = self._handle_boundary(moment, current)
            self._last_key = key
            return result

    def _handle_boundary(
        self, now: datetime, current: Optional[SessionWindow]
    ) -> ChangeResult:
        result = ChangeResult()
        today = local_date(now)

        with self.session_factory() as db, db.begin():
            open_records = db.scalars(
                select(OccupancyRecord)
                .where(OccupancyRecord.occupied_until.is_(None))
                .with_for_update()
            ).all()

            # 1. Close everything that does not belong to the current session.
            for record in open_records:
                runtime = self.seats_by_id.get(record.seat_id)
                window = self.session_manager.window_by_id(record.session_id)

                if (
                    current is not None
                    and window is not None
                    and record.session_id == current.id
                    and record.session_date == today
                ):
                    if runtime is not None:
                        runtime.state = OCCUPIED
                        runtime.open_record_id = record.id
                        runtime.open_session_id = record.session_id
                        runtime.open_from = ensure_local(record.occupied_from)
                    continue

                end = window.end_at(record.session_date) if window else now
                end = ensure_local(end)
                if end > now:
                    end = now
                start = ensure_local(record.occupied_from)
                if end < start:
                    end = start
                record.occupied_until = end

                if runtime is not None:
                    if runtime.open_record_id == record.id:
                        runtime.open_record_id = None
                        runtime.open_session_id = None
                        runtime.open_from = None
                    result.events.append(
                        self._event(
                            "leave",
                            runtime,
                            window,
                            end,
                            reason="session_boundary",
                            record_id=record.id,
                            until=end,
                        )
                    )
                result.changed = True

            # 2. Open a continuation record for seats still physically present.
            if current is not None:
                start = current.start_at(today)
                for runtime in self.seats.values():
                    if runtime.open_record_id is not None:
                        continue
                    if not self._is_present(runtime, now):
                        continue
                    record = OccupancyRecord(
                        seat_id=runtime.seat_id,
                        session_id=current.id,
                        session_date=today,
                        occupied_from=start,
                        occupied_until=None,
                    )
                    db.add(record)
                    db.flush()
                    runtime.open_record_id = record.id
                    runtime.open_session_id = current.id
                    runtime.open_from = start
                    result.changed = True
                    result.presence_changed = True
                    result.events.append(
                        self._event(
                            "enter",
                            runtime,
                            current,
                            start,
                            reason="session_boundary",
                            record_id=record.id,
                        )
                    )

        return result

    # -------------------------------------------------------------- queries
    def runtime_map(self) -> dict[str, SeatRuntime]:
        with self._lock:
            return dict(self.seats)

    def snapshot(self) -> dict:
        """Realtime payload broadcast to browsers.

        ``seats`` contains seats that are physically present (used to drive the
        avatars, including during breaks). ``occupied_seats`` contains seats
        that currently have an OPEN database record.
        """
        with self._lock:
            now = self._now()
            present = sorted(
                code for code, rt in self.seats.items() if rt.state == OCCUPIED
            )
            occupied = sorted(
                code for code, rt in self.seats.items() if rt.open_record_id is not None
            )
            total = len(self.seats)
            stats = {
                "total_seats": total,
                "occupied_seats": len(occupied),
                "empty_seats": total - len(occupied),
                "present_seats": len(present),
                "occupancy_percentage": round(
                    (len(occupied) / total * 100.0) if total else 0.0, 2
                ),
            }
            context = self.session_manager.context(now)
            return {
                "type": "occupancy",
                "seats": present,
                "occupied_seats": occupied,
                "statistics": stats,
                "session": context["session"],
                "session_date": context["session_date"],
                "local_time": context["local_time"],
                "timezone": context["timezone"],
                "is_break": context["is_break"],
                "generated_at": now.isoformat(),
            }

    def session_context(self, now: Optional[datetime] = None) -> dict:
        moment = self._now() if now is None else ensure_local(now)
        return self.session_manager.context(moment)

    def statistics(self) -> dict:
        return self.snapshot()["statistics"]

    def current_presence(self) -> dict:
        with self._lock:
            return {
                code: rt.state == OCCUPIED for code, rt in self.seats.items()
            }
