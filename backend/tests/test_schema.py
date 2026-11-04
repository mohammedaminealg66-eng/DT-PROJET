"""Schema and seeding tests."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import OccupancyRecord, Seat
from app.models import Session as SessionModel
from tests.conftest import TZ


def test_seed_has_24_seats_and_4_sessions(clean_db):
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM seats")) == 24
        assert conn.scalar(text("SELECT count(*) FROM sessions")) == 4

        codes = [
            row[0]
            for row in conn.execute(
                text("SELECT seat_code FROM seats ORDER BY column_number, row_number")
            )
        ]
        assert codes[0] == "C1-R1"
        assert codes[-1] == "C4-R6"
        assert len(set(codes)) == 24


def test_sessions_match_the_specification(clean_db):
    with clean_db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT session_number, start_time, end_time "
                "FROM sessions ORDER BY session_number"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        (1, datetime.strptime("08:00", "%H:%M").time(), datetime.strptime("10:30", "%H:%M").time()),
        (2, datetime.strptime("10:30", "%H:%M").time(), datetime.strptime("13:00", "%H:%M").time()),
        (3, datetime.strptime("13:30", "%H:%M").time(), datetime.strptime("16:00", "%H:%M").time()),
        (4, datetime.strptime("16:00", "%H:%M").time(), datetime.strptime("18:30", "%H:%M").time()),
    ]


def test_partial_unique_index_prevents_two_open_records(clean_db, session_factory):
    with clean_db.connect() as conn:
        index_count = conn.scalar(
            text("SELECT count(*) FROM pg_indexes WHERE indexname = 'uq_occ_open'")
        )
    assert index_count == 1

    with session_factory() as db:
        seat_id = db.scalar(select(Seat.id).where(Seat.seat_code == "C1-R1"))
        session_id = db.scalar(
            select(SessionModel.id).where(SessionModel.session_number == 1)
        )

    def open_record():
        return OccupancyRecord(
            seat_id=seat_id,
            session_id=session_id,
            session_date=date(2026, 11, 2),
            occupied_from=datetime(2026, 11, 2, 8, 10, tzinfo=TZ),
            occupied_until=None,
        )

    with session_factory() as db:
        db.add(open_record())
        db.commit()

    with pytest.raises(IntegrityError):
        with session_factory() as db:
            db.add(open_record())
            db.commit()

    # Closing the first record allows a new open record (Rule C).
    with session_factory() as db:
        record = db.scalar(
            select(OccupancyRecord).where(OccupancyRecord.occupied_until.is_(None))
        )
        record.occupied_until = datetime(2026, 11, 2, 8, 30, tzinfo=TZ)
        db.commit()

    with session_factory() as db:
        db.add(open_record())
        db.commit()

    with session_factory() as db:
        total = db.scalar(
            select(func.count()).select_from(OccupancyRecord)
        )
        opened = db.scalars(
            select(OccupancyRecord).where(OccupancyRecord.occupied_until.is_(None))
        ).all()
    assert total == 2
    assert len(opened) == 1
