"""Test helper functions (no fixtures here)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select

from app.models import OccupancyRecord, Seat
from app.models import Session as SessionModel
from app.timeutil import ensure_local


def record_dicts(session_factory) -> list[dict]:
    """All occupancy records as plain dicts, oldest first."""
    with session_factory() as db:
        rows = db.execute(
            select(OccupancyRecord, Seat.seat_code, SessionModel.session_number)
            .join(Seat, OccupancyRecord.seat_id == Seat.id)
            .join(SessionModel, OccupancyRecord.session_id == SessionModel.id)
            .order_by(OccupancyRecord.occupied_from, OccupancyRecord.id)
        ).all()
        return [
            {
                "id": record.id,
                "seat": seat_code,
                "session": session_number,
                "date": record.session_date,
                "from": ensure_local(record.occupied_from),
                "until": ensure_local(record.occupied_until)
                if record.occupied_until
                else None,
                "is_open": record.occupied_until is None,
            }
            for record, seat_code, session_number in rows
        ]


def open_records(session_factory) -> list[dict]:
    return [record for record in record_dicts(session_factory) if record["is_open"]]


def closed_records(session_factory) -> list[dict]:
    return [record for record in record_dicts(session_factory) if not record["is_open"]]


def seat_date(value: datetime) -> date:
    return ensure_local(value).date()


def parse_iso(value: str) -> datetime:
    """Parse an ISO timestamp, accepting the `Z` suffix on Python 3.10.

    Pydantic serializes a UTC offset as `Z`, and `datetime.fromisoformat`
    only understands it from Python 3.11 on.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
