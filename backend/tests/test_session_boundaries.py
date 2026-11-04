"""Session boundary tests (rules 3 and 6)."""

from __future__ import annotations

from datetime import time

from tests.conftest import at
from tests.helpers import open_records, record_dicts


def test_3_seat_carried_across_1030_boundary(manager, session_factory):
    twin, _clock = manager

    twin.observe("C1-R1", True, at(8, 10, 0))
    twin.observe("C1-R1", True, at(8, 10, 2))

    # Continuous detection right up to the boundary.
    twin.observe("C1-R1", True, at(10, 29, 58))
    twin.observe("C1-R1", True, at(10, 29, 59))

    result = twin.tick(at(10, 30, 0))
    assert result.changed is True

    records = record_dicts(session_factory)
    assert len(records) == 2

    session_1 = records[0]
    assert session_1["session"] == 1
    assert session_1["from"].time() == time(8, 10)
    assert session_1["until"].time() == time(10, 30)
    assert not session_1["is_open"]

    session_2 = records[1]
    assert session_2["session"] == 2
    assert session_2["from"].time() == time(10, 30)
    assert session_2["until"] is None
    assert session_2["is_open"]

    # The seat must remain occupied, not be marked empty.
    assert twin.seats["C1-R1"].state == "OCCUPIED"
    assert twin.snapshot()["seats"] == ["C1-R1"]


def test_6_no_records_during_the_1300_1330_break(manager, session_factory):
    twin, _clock = manager

    twin.observe("C1-R1", True, at(8, 10, 0))
    twin.observe("C1-R1", True, at(8, 10, 2))

    twin.observe("C1-R1", True, at(10, 29, 59))
    twin.tick(at(10, 30, 0))

    twin.observe("C1-R1", True, at(12, 59, 59))
    twin.tick(at(13, 0, 0))

    # Break: the person is still detected, but no record may be created.
    twin.observe("C1-R1", True, at(13, 10, 0))
    twin.observe("C1-R1", True, at(13, 10, 2))
    assert open_records(session_factory) == []

    records = record_dicts(session_factory)
    for record in records:
        local_from = record["from"].time()
        assert not (time(13, 0) <= local_from < time(13, 30)), (
            "record created during the break: %r" % (record,)
        )

    # Still present when session 3 starts at 13:30.
    twin.observe("C1-R1", True, at(13, 29, 58))
    twin.observe("C1-R1", True, at(13, 29, 59))
    twin.tick(at(13, 30, 0))

    records = record_dicts(session_factory)
    assert len(records) == 3
    session_3 = records[-1]
    assert session_3["session"] == 3
    assert session_3["from"].time() == time(13, 30)
    assert session_3["is_open"]

    assert twin.seats["C1-R1"].state == "OCCUPIED"


def test_no_records_after_1830(manager, session_factory):
    twin, _clock = manager

    twin.observe("C1-R1", True, at(16, 0, 0))
    twin.observe("C1-R1", True, at(16, 0, 2))
    assert len(open_records(session_factory)) == 1

    twin.observe("C1-R1", True, at(18, 29, 59))
    result = twin.tick(at(18, 30, 0))
    assert result.changed is True
    assert open_records(session_factory) == []

    twin.observe("C1-R1", True, at(18, 40, 0))
    twin.observe("C1-R1", True, at(18, 40, 2))

    records = record_dicts(session_factory)
    assert len(records) == 1
    assert records[0]["until"].time() == time(18, 30)
    for record in records:
        assert record["from"].time() < time(18, 30)
