#!/usr/bin/env python3
"""Simulate an external occupancy producer pushing snapshots to the backend.

This exercises the REAL live path (hysteresis + PostgreSQL + WebSocket),
unlike the browser TEST mode where the same snapshots come from the manual
3D controls. In a later phase YOLO/the Raspberry Pi will replace this script
without changing the backend.

Usage (from the repository root):

    python backend/scripts/simulate_occupancy.py
    python backend/scripts/simulate_occupancy.py --api http://127.0.0.1:8000 --scenario steady
    python backend/scripts/simulate_occupancy.py --token secret

Scenarios:
    walk    seats enter one by one, then leave (loop)
    steady  three seats stay occupied
    empty   nobody in the room
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def post_snapshot(api: str, seats: list[str], token: str | None) -> dict:
    url = api.rstrip("/") + "/api/ingest/snapshot"
    body = json.dumps({"seats": seats}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Ingest-Token"] = token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def scenario_seats(name: str, step: int) -> list[str]:
    if name == "empty":
        return []
    if name == "steady":
        return ["C1-R1", "C2-R2", "C3-R3"]

    cycle = [
        [],
        ["C1-R1"],
        ["C1-R1", "C2-R2"],
        ["C1-R1", "C2-R2", "C3-R3"],
        ["C2-R2", "C3-R3"],
        ["C3-R3"],
        [],
    ]
    # 10 ticks per phase (5 s at 0.5 s interval)
    return cycle[(step // 10) % len(cycle)]


def main() -> int:
    parser = argparse.ArgumentParser(description="External occupancy producer")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--token", default=None)
    parser.add_argument(
        "--scenario", default="walk", choices=["walk", "steady", "empty"]
    )
    parser.add_argument(
        "--steps", type=int, default=0, help="0 = run forever"
    )
    args = parser.parse_args()

    print(f"[simulate_occupancy] API={args.api} scenario={args.scenario}")
    step = 0
    while args.steps == 0 or step < args.steps:
        seats = scenario_seats(args.scenario, step)
        try:
            result = post_snapshot(args.api, seats, args.token)
            state = result.get("state", {})
            print(
                f"[simulate_occupancy] step={step:4d} sent={len(seats):2d} "
                f"present={len(state.get('seats', [])):2d} "
                f"occupied={len(state.get('occupied_seats', [])):2d} "
                f"changed={result.get('changed')}"
            )
            for event in result.get("events", []):
                print(
                    f"    {event['action']:<5} {event['seat']} "
                    f"session={event['session_number']} reason={event['reason']}"
                )
        except urllib.error.HTTPError as error:
            print(f"[simulate_occupancy] HTTP {error.code}: {error.read().decode('utf-8', 'replace')}")
            return 1
        except urllib.error.URLError as error:
            print(f"[simulate_occupancy] backend unreachable: {error.reason}")
            time.sleep(1.0)
            continue

        step += 1
        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
