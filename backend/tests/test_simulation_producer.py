"""Tests for the manual simulation producer (this development phase).

The 3D frontend does not detect anything: its buttons only maintain a
"desired detection set" and POST that full snapshot to
``/api/ingest/snapshot`` every 250 ms. These tests reproduce that heartbeat
and check that the backend applies the real 2 s / 3 s hysteresis and writes
PostgreSQL records.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeClock, at
from tests.helpers import parse_iso


@pytest.fixture()
def sim_client(clean_db):
    """API client with the production 2 s / 3 s confirmation delays."""
    from app.main import app

    clock = FakeClock(at(9, 0, 0))
    with TestClient(app) as test_client:
        manager = app.state.manager
        manager.clock = clock
        manager.occupied_confirm_seconds = 2.0
        manager.empty_confirm_seconds = 3.0
        test_client.twin_clock = clock
        yield test_client


def snapshot(client, seats):
    response = client.post("/api/ingest/snapshot", json={"seats": seats})
    assert response.status_code == 200, response.text
    return response.json()


def history(client, **params):
    response = client.get("/api/occupancy/history", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_snapshot_heartbeat_confirms_after_two_seconds(sim_client):
    """Repeated identical snapshots open exactly one record at t0 + 2 s."""
    client = sim_client

    snapshot(client, ["C1-R1"])
    assert history(client) == []

    client.twin_clock.set(at(9, 0, 1))
    snapshot(client, ["C1-R1"])
    assert history(client) == []

    client.twin_clock.set(at(9, 0, 2))
    snapshot(client, ["C1-R1"])

    rows = history(client)
    assert len(rows) == 1
    assert rows[0]["seat_code"] == "C1-R1"
    assert rows[0]["is_open"] is True
    # Hysteresis uses the START of the continuous detection.
    assert parse_iso(rows[0]["occupied_from"]) == at(9, 0, 0)

    # The 250 ms heartbeat keeps repeating the same snapshot: one record only.
    for second in range(3, 12):
        client.twin_clock.set(at(9, 0, second))
        snapshot(client, ["C1-R1"])

    rows = history(client)
    assert len(rows) == 1
    assert rows[0]["is_open"] is True

    stats = client.get("/api/statistics").json()
    assert stats["occupied_seats"] == 1
    assert stats["empty_seats"] == 23


def test_snapshot_absence_closes_after_three_seconds(sim_client):
    """Empty snapshots close the record only after 3 s of continuous absence."""
    client = sim_client

    snapshot(client, ["C2-R3"])
    client.twin_clock.set(at(9, 0, 2))
    snapshot(client, ["C2-R3"])
    assert len(history(client, open_only="true")) == 1

    client.twin_clock.set(at(9, 5, 0))
    snapshot(client, [])
    client.twin_clock.set(at(9, 5, 2))
    snapshot(client, [])
    assert len(history(client, open_only="true")) == 1

    client.twin_clock.set(at(9, 5, 3))
    snapshot(client, [])

    rows = history(client)
    assert len(rows) == 1
    assert rows[0]["is_open"] is False
    # Closure timestamp is the START of the continuous absence.
    assert parse_iso(rows[0]["occupied_until"]) == at(9, 5, 0)
    assert history(client, open_only="true") == []
    assert client.get("/api/statistics").json()["occupied_seats"] == 0


def test_fill_clear_then_return_creates_separate_records(sim_client):
    """The "Tout remplir" / "Vider" buttons behave like real occupancy."""
    client = sim_client
    all_seats = [row["seat_code"] for row in client.get("/api/seats").json()]
    assert len(all_seats) == 24

    snapshot(client, all_seats)
    client.twin_clock.set(at(9, 0, 2))
    snapshot(client, all_seats)

    rows = history(client, limit=100)
    assert len(rows) == 24
    assert all(row["is_open"] for row in rows)

    # "Vider" -> empty snapshots for 3 s close every record.
    client.twin_clock.set(at(9, 10, 0))
    snapshot(client, [])
    client.twin_clock.set(at(9, 10, 3))
    snapshot(client, [])

    rows = history(client, limit=100)
    assert len(rows) == 24
    assert all(row["is_open"] is False for row in rows)
    assert all(
        parse_iso(row["occupied_until"]) == at(9, 10, 0)
        for row in rows
    )

    # A returning student creates a NEW record (never merged).
    client.twin_clock.set(at(9, 20, 0))
    snapshot(client, ["C1-R1"])
    client.twin_clock.set(at(9, 20, 2))
    snapshot(client, ["C1-R1"])

    for_seat = history(client, seat="C1-R1")
    assert len(for_seat) == 2
    closed = [row for row in for_seat if not row["is_open"]]
    opened = [row for row in for_seat if row["is_open"]]
    assert len(closed) == 1 and len(opened) == 1
    assert parse_iso(opened[0]["occupied_from"]) == at(9, 20, 0)
