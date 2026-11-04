"""Restart recovery test (rule 7)."""

from __future__ import annotations

from datetime import time

from app.services.occupancy_manager import OccupancyManager
from tests.conftest import FakeClock, at
from tests.helpers import open_records, record_dicts


def test_7_restart_recovers_open_record_without_duplicates(
    manager, session_factory, session_manager
):
    twin, _clock = manager

    twin.observe("C1-R1", True, at(8, 10, 0))
    twin.observe("C1-R1", True, at(8, 10, 2))
    assert len(open_records(session_factory)) == 1
    record_id = open_records(session_factory)[0]["id"]

    # Simulate a process restart at 09:00 (same day, same session).
    restarted = OccupancyManager(
        session_factory,
        session_manager,
        clock=FakeClock(at(9, 0, 0)),
        occupied_confirm_seconds=2.0,
        empty_confirm_seconds=3.0,
    )

    assert restarted.recovered_seats == ["C1-R1"]
    assert restarted.seats["C1-R1"].state == "OCCUPIED"

    recovered = open_records(session_factory)
    assert len(recovered) == 1
    assert recovered[0]["id"] == record_id

    # Further detections must not duplicate the record.
    restarted.observe("C1-R1", True, at(9, 0, 1))
    restarted.observe("C1-R1", True, at(9, 0, 3))
    assert len(open_records(session_factory)) == 1

    # Closing after the restart works and preserves history.
    restarted.observe("C1-R1", False, at(9, 30, 0))
    restarted.observe("C1-R1", False, at(9, 30, 3))

    records = record_dicts(session_factory)
    assert len(records) == 1
    assert records[0]["id"] == record_id
    assert records[0]["until"].time() == time(9, 30)
    assert not records[0]["is_open"]


def test_restart_in_a_later_session_closes_stale_record(
    manager, session_factory, session_manager
):
    twin, _clock = manager
    twin.observe("C2-R2", True, at(8, 10, 0))
    twin.observe("C2-R2", True, at(8, 10, 2))

    # Restart at 11:00: the session 1 record must be closed at 10:30 and no
    # session 2 record invented (we cannot know the person is still there).
    restarted = OccupancyManager(
        session_factory,
        session_manager,
        clock=FakeClock(at(11, 0, 0)),
        occupied_confirm_seconds=2.0,
        empty_confirm_seconds=3.0,
    )

    assert restarted.recovered_seats == []
    assert restarted.seats["C2-R2"].state == "EMPTY"
    assert open_records(session_factory) == []

    records = record_dicts(session_factory)
    assert len(records) == 1
    assert records[0]["until"].time() == time(10, 30)
    assert not records[0]["is_open"]
