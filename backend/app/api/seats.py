"""Seat endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import OccupancyRecord, Seat
from ..schemas import SeatOut
from ..timeutil import ensure_local

router = APIRouter(tags=["seats"])


@router.get("/seats", response_model=list[SeatOut])
def list_seats(request: Request, db: OrmSession = Depends(get_db)) -> list[SeatOut]:
    """All 24 seats with their current database occupancy + live presence."""
    manager = request.app.state.manager
    runtimes = manager.runtime_map()

    seats = db.scalars(
        select(Seat).order_by(Seat.column_number, Seat.row_number)
    ).all()
    open_records = {
        record.seat_id: record
        for record in db.scalars(
            select(OccupancyRecord).where(OccupancyRecord.occupied_until.is_(None))
        ).all()
    }

    result: list[SeatOut] = []
    for seat in seats:
        runtime = runtimes.get(seat.seat_code)
        record = open_records.get(seat.id)
        result.append(
            SeatOut(
                id=seat.id,
                seat_code=seat.seat_code,
                row_number=seat.row_number,
                column_number=seat.column_number,
                occupied=record is not None,
                present=bool(runtime and runtime.state == "OCCUPIED"),
                occupied_since=ensure_local(record.occupied_from) if record else None,
                open_record_id=record.id if record else None,
            )
        )
    return result
