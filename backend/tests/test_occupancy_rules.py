"""Tests for the occupancy record rules 1, 2, 4 and 5."""

from __future__ import annotations

from datetime import time

from tests.conftest import at
from tests.helpers import closed_records, open_records, record_dicts


def test_1_person_sits_then_leaves_creates_one_closed_record(manager, session_factory):
    twin, _clock = manager

    twin.observe("C1-R1", True, at(8, 10, 0))
    twin.observe("C1-R1", True, at(8, 10, 2))

    open_now = open_records(session_factory)
    assert len(open_now) == 1
    assert open_now[0]["seat"] == "C1-R1"
    assert open_now[0]["session"] == 1
    assert open_now[0]["from"].time() == time(8, 10)
    assert open_now[0]["until"] is None

    twin.observe("C1-R1", False, at(8, 30, 0))
    twin.observe("C1-R1", False, at(8, 30, 3))

    records = record_dicts(session_factory)
    assert len(records) == 1
    assert records[0]["until"].time() == time(8, 30)
    assert not records[0]["is_open"]
    assert twin.seats["C1-R1"].state == "EMPTY"


def test_2_returning_to_same_seat_creates_a_second_record(manager, session_factory):
    twin, _clock = manager

    twin.observe("C1-R1", True, at(8, 10, 0))
    twin.observe("C1-R1", True, at(8, 10, 2))
    twin.observe("C1-R1", False, at(8, 30, 0))
    twin.observe("C1-R1", False, at(8, 30, 3))

    twin.observe("C1-R1", True, at(8, 40, 0))
    twin.observe("C1-R1", True, at(8, 40, 2))

    records = record_dicts(session_factory)
    assert len(records) == 2
    assert records[0]["from"].time() == time(8, 10)
    assert records[0]["until"].time() == time(8, 30)
    assert records[1]["from"].time() == time(8, 40)
    assert records[1]["until"] is None


def test_4_repeated_detections_while_occupied_create_only_one_record(
    manager, session_factory
):
    twin, _clock = manager

    twin.observe("C2-R3", True, at(9, 0, 0))
    twin.observe("C2-R3", True, at(9, 0, 2))

    # 40 additional detections: no duplicates allowed.
    for step in range(40):
        twin.observe("C2-R3", True, at(9, 0, 3 + step))

    assert len(record_dicts(session_factory)) == 1
    assert len(open_records(session_factory)) == 1


def test_5_short_detection_loss_does_not_close_the_record(manager, session_factory):
    twin, _clock = manager

    twin.observe("C3-R2", True, at(9, 0, 0))
    twin.observe("C3-R2", True, at(9, 0, 2))
    assert len(open_records(session_factory)) == 1

    # Person is not detected for 2 seconds (threshold is 3 seconds).
    twin.observe("C3-R2", False, at(9, 5, 0))
    twin.observe("C3-R2", False, at(9, 5, 2))
    assert len(open_records(session_factory)) == 1
    assert twin.seats["C3-R2"].state == "OCCUPIED"

    # Detection comes back before the threshold: still one open record.
    twin.observe("C3-R2", True, at(9, 5, 3))
    assert len(open_records(session_factory)) == 1
    assert twin.seats["C3-R2"].pending_since is None

    # Real absence for >= 3 seconds closes it.
    twin.observe("C3-R2", False, at(9, 10, 0))
    twin.observe("C3-R2", False, at(9, 10, 3))
    records = record_dicts(session_factory)
    assert len(records) == 1
    assert records[0]["until"].time() == time(9, 10)
    assert not records[0]["is_open"]


def test_hysteresis_thresholds_are_configurable(session_factory, session_manager):
    from app.services.occupancy_manager import OccupancyManager
    from tests.conftest import FakeClock

    clock = FakeClock(at(8, 0))
    twin = OccupancyManager(
        session_factory,
        session_manager,
        clock=clock,
        occupied_confirm_seconds=5.0,
        empty_confirm_seconds=8.0,
    )

    twin.observe("C1-R1", True, at(8, 10, 0))
    twin.observe("C1-R1", True, at(8, 10, 4))
    assert len(record_dicts(session_factory)) == 0

    twin.observe("C1-R1", True, at(8, 10, 5))
    assert len(open_records(session_factory)) == 1

    twin.observe("C1-R1", False, at(8, 20, 0))
    twin.observe("C1-R1", False, at(8, 20, 7))
    assert len(open_records(session_factory)) == 1

    twin.observe("C1-R1", False, at(8, 20, 8))
    assert len(closed_records(session_factory)) == 1
