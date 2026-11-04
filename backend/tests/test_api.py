"""API + WebSocket tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeClock, at


@pytest.fixture()
def client(clean_db):
    from app.main import app

    clock = FakeClock(at(9, 0, 0))
    with TestClient(app) as test_client:
        app.state.manager.clock = clock
        test_client.twin_clock = clock
        yield test_client


def test_health_and_reference_endpoints(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database"] is True

    seats = client.get("/api/seats").json()
    assert len(seats) == 24
    assert {seat["seat_code"] for seat in seats} >= {"C1-R1", "C4-R6"}
    assert all(seat["occupied"] is False for seat in seats)

    sessions = client.get("/api/sessions").json()
    assert [session["session_number"] for session in sessions] == [1, 2, 3, 4]

    current = client.get("/api/sessions/current").json()
    assert current["session"]["session_number"] == 1
    assert current["timezone"] == "Africa/Casablanca"
    assert current["is_break"] is False


def test_ingest_snapshot_creates_records_and_updates_statistics(client):
    response = client.post(
        "/api/ingest/snapshot", json={"seats": ["C1-R1", "C2-R3"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["changed"] is True

    current = client.get("/api/occupancy/current").json()
    assert set(current["occupied_seats"]) == {"C1-R1", "C2-R3"}
    assert set(current["seats"]) == {"C1-R1", "C2-R3"}

    stats = client.get("/api/statistics").json()
    assert stats["total_seats"] == 24
    assert stats["occupied_seats"] == 2
    assert stats["empty_seats"] == 22
    assert stats["occupancy_percentage"] == pytest.approx(8.33, abs=0.01)

    history = client.get("/api/occupancy/history").json()
    assert len(history) == 2
    assert all(record["is_open"] for record in history)
    assert {record["seat_code"] for record in history} == {"C1-R1", "C2-R3"}

    # Leaving later closes the records.
    client.twin_clock.set(at(9, 30, 0))
    response = client.post("/api/ingest/snapshot", json={"seats": []})
    assert response.status_code == 200
    assert response.json()["changed"] is True

    history = client.get(
        "/api/occupancy/history?date=2026-11-02&session_number=1"
    ).json()
    assert len(history) == 2
    assert all(record["is_open"] is False for record in history)
    assert all(record["occupied_until"] for record in history)

    stats = client.get("/api/statistics").json()
    assert stats["occupied_seats"] == 0


def test_history_filters(client):
    client.post("/api/ingest/snapshot", json={"seats": ["C1-R1", "C4-R6"]})
    client.twin_clock.set(at(9, 30, 0))
    client.post("/api/ingest/snapshot", json={"seats": ["C4-R6"]})

    by_seat = client.get("/api/occupancy/history?seat=C1-R1").json()
    assert len(by_seat) == 1
    assert by_seat[0]["is_open"] is False

    open_only = client.get("/api/occupancy/history?open_only=true").json()
    assert len(open_only) == 1
    assert open_only[0]["seat_code"] == "C4-R6"

    empty_day = client.get("/api/occupancy/history?date=2026-11-03").json()
    assert empty_day == []


def test_unknown_seat_is_rejected(client):
    response = client.post(
        "/api/ingest/event", json={"seat": "X9-R9", "action": "enter"}
    )
    assert response.status_code == 404

    response = client.post(
        "/api/ingest/event", json={"seat": "C1-R1", "action": "dance"}
    )
    assert response.status_code == 422


def test_websocket_sends_snapshot_and_broadcasts(client):
    with client.websocket_connect("/ws") as websocket:
        first = websocket.receive_json()
        assert first["type"] == "occupancy"
        assert first["seats"] == []

        client.twin_clock.set(at(9, 5, 0))
        client.post("/api/ingest/snapshot", json={"seats": ["C3-R3"]})

        received = None
        for _ in range(5):
            message = websocket.receive_json()
            if message.get("type") == "occupancy" and "C3-R3" in message.get(
                "seats", []
            ):
                received = message
                break

        assert received is not None, "no occupancy broadcast received"
        assert received["statistics"]["occupied_seats"] == 1
        assert received["session"]["session_number"] == 1
