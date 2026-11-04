"""Database models: seats, sessions and occupancy_records."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Seat(Base):
    __tablename__ = "seats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seat_code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    column_number: Mapped[int] = mapped_column(Integer, nullable=False)

    records: Mapped[List["OccupancyRecord"]] = relationship(
        back_populates="seat", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("row_number", "column_number", name="uq_seats_row_col"),
        CheckConstraint("row_number BETWEEN 1 AND 6", name="ck_seats_row"),
        CheckConstraint("column_number BETWEEN 1 AND 4", name="ck_seats_col"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Seat {self.seat_code}>"


class Session(Base):
    """A recurring daily time slot (not an individual lesson)."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    records: Mapped[List["OccupancyRecord"]] = relationship(back_populates="session")

    __table_args__ = (
        CheckConstraint("start_time < end_time", name="ck_sessions_range"),
        CheckConstraint("session_number BETWEEN 1 AND 4", name="ck_sessions_number"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Session {self.session_number} {self.start_time}-{self.end_time}>"


class OccupancyRecord(Base):
    """One continuous period during which a seat is occupied."""

    __tablename__ = "occupancy_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seat_id: Mapped[int] = mapped_column(
        ForeignKey("seats.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="RESTRICT"), nullable=False
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    occupied_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occupied_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    seat: Mapped[Seat] = relationship(back_populates="records")
    session: Mapped[Session] = relationship(back_populates="records")

    __table_args__ = (
        CheckConstraint(
            "occupied_until IS NULL OR occupied_until >= occupied_from",
            name="ck_occ_until",
        ),
        # At most ONE open record per seat / session / date.
        Index(
            "uq_occ_open",
            "seat_id",
            "session_id",
            "session_date",
            unique=True,
            postgresql_where=text("occupied_until IS NULL"),
        ),
        Index("ix_occ_date_session", "session_date", "session_id"),
        Index("ix_occ_seat_date", "seat_id", "session_date"),
        Index(
            "ix_occ_open_seat",
            "seat_id",
            postgresql_where=text("occupied_until IS NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<OccupancyRecord seat={self.seat_id} session={self.session_id} "
            f"{self.occupied_from} -> {self.occupied_until}>"
        )
