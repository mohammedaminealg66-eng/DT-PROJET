"""Occupancy endpoints (current state + history)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import OccupancyRecord, Seat
from ..models import Session as SessionModel
from ..schemas import OccupancyRecordOut
from ..timeutil import ensure_local, now_local

router = APIRouter(prefix="/occupancy", tags=["occupancy"])


@router.get("/current")
def current_occupancy(request: Request) -> dict:
    """Live presence + open database records + session context."""
    manager = request.app.state.manager
    snapshot = manager.snapshot()
    return {
        "seats": snapshot["seats"],
        "occupied_seats": snapshot["occupied_seats"],
        "statistics": snapshot["statistics"],
        "session": snapshot["session"],
        "session_date": snapshot["session_date"],
        "local_time": snapshot["local_time"],
        "timezone": snapshot["timezone"],
        "is_break": snapshot["is_break"],
        "generated_at": snapshot["generated_at"],
    }


@router.get("/history", response_model=list[OccupancyRecordOut])
def occupancy_history(
    db: OrmSession = Depends(get_db),
    on: Optional[date] = Query(None, alias="date", description="Filter by session_date"),
    session_id: Optional[int] = Query(None),
    session_number: Optional[int] = Query(None, ge=1, le=4),
    seat_code: Optional[str] = Query(None, description="Example: C1-R3"),
    seat: Optional[str] = Query(None, description="Alias for seat_code"),
    open_only: bool = Query(False, description="Only records with occupied_until IS NULL"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    """Historical occupancy records, newest first."""
    code = seat_code or seat

    stmt = (
        select(OccupancyRecord, Seat.seat_code, SessionModel.session_number)
        .join(Seat, OccupancyRecord.seat_id == Seat.id)
        .join(SessionModel, OccupancyRecord.session_id == SessionModel.id)
    )
    if on is not None:
        stmt = stmt.where(OccupancyRecord.session_date == on)
    if session_id is not None:
        stmt = stmt.where(OccupancyRecord.session_id == session_id)
    if session_number is not None:
        stmt = stmt.where(SessionModel.session_number == session_number)
    if code:
        stmt = stmt.where(Seat.seat_code == code)
    if open_only:
        stmt = stmt.where(OccupancyRecord.occupied_until.is_(None))

    stmt = (
        stmt.order_by(OccupancyRecord.occupied_from.desc(), OccupancyRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )

    now = now_local()
    rows = db.execute(stmt).all()
    result: list[dict] = []
    for record, seat_code_value, session_number_value in rows:
        start = ensure_local(record.occupied_from)
        end = ensure_local(record.occupied_until) if record.occupied_until else None
        duration = ((end or now) - start).total_seconds()
        result.append(
            {
                "id": record.id,
                "seat_id": record.seat_id,
                "seat_code": seat_code_value,
                "session_id": record.session_id,
                "session_number": session_number_value,
                "session_date": record.session_date,
                "occupied_from": start,
                "occupied_until": end,
                "duration_seconds": round(duration, 1),
                "is_open": end is None,
                "created_at": ensure_local(record.created_at),
            }
        )
    return result
