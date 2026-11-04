"""Pydantic request / response schemas."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from pydantic import BaseModel, Field


class SeatOut(BaseModel):
    id: int
    seat_code: str
    row_number: int
    column_number: int
    occupied: bool
    present: bool
    occupied_since: Optional[datetime] = None
    open_record_id: Optional[int] = None


class SessionOut(BaseModel):
    id: int
    session_number: int
    start_time: time
    end_time: time

    model_config = {"from_attributes": True}


class SessionContextOut(BaseModel):
    session: Optional[dict] = None
    session_date: str
    local_time: str
    timezone: str
    is_break: bool
    before_hours: bool
    after_hours: bool
    next_session_start: Optional[str] = None


class OccupancyRecordOut(BaseModel):
    id: int
    seat_id: int
    seat_code: str
    session_id: int
    session_number: int
    session_date: date
    occupied_from: datetime
    occupied_until: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    is_open: bool
    created_at: datetime


class StatisticsOut(BaseModel):
    total_seats: int
    occupied_seats: int
    empty_seats: int
    present_seats: int
    occupancy_percentage: float
    session: Optional[dict] = None
    session_date: str
    is_break: bool
    records_today: int = 0


class IngestSnapshotIn(BaseModel):
    seats: list[str] = Field(default_factory=list, description="Full list of occupied seat codes")
    at: Optional[datetime] = Field(
        default=None, description="Optional ISO timestamp (server clock if omitted)"
    )


class IngestEventIn(BaseModel):
    seat: str
    action: str = Field(description="'enter' or 'leave' (a single observation)")
    at: Optional[datetime] = None


class IngestResultOut(BaseModel):
    ok: bool
    changed: bool
    presence_changed: bool
    events: list[dict]
    unknown: list[str]
    state: dict
