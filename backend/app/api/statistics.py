"""Statistics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import OccupancyRecord, Seat
from ..schemas import StatisticsOut
from ..timeutil import local_date, now_local

router = APIRouter(tags=["statistics"])


@router.get("/statistics", response_model=StatisticsOut)
def statistics(request: Request, db: OrmSession = Depends(get_db)) -> StatisticsOut:
    manager = request.app.state.manager
    now = now_local()
    today = local_date(now)

    total = int(db.scalar(select(func.count()).select_from(Seat)) or 0)
    open_count = int(
        db.scalar(
            select(func.count())
            .select_from(OccupancyRecord)
            .where(OccupancyRecord.occupied_until.is_(None))
        )
        or 0
    )
    records_today = int(
        db.scalar(
            select(func.count())
            .select_from(OccupancyRecord)
            .where(OccupancyRecord.session_date == today)
        )
        or 0
    )
    present = sum(1 for value in manager.current_presence().values() if value)
    context = manager.session_context(now)

    return StatisticsOut(
        total_seats=total,
        occupied_seats=open_count,
        empty_seats=max(0, total - open_count),
        present_seats=present,
        occupancy_percentage=round((open_count / total * 100.0) if total else 0.0, 2),
        session=context["session"],
        session_date=context["session_date"],
        is_break=context["is_break"],
        records_today=records_today,
    )
