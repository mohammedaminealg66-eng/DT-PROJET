"""Raspberry Pi -> backend ingest endpoints.

The Pi only needs to push FULL occupancy snapshots (2 Hz is plenty):

    POST /api/ingest/snapshot
    {"seats": ["C1-R1", "C2-R4"]}

The backend applies hysteresis, session boundaries and persistence, then
broadcasts the new state to every connected browser.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..schemas import IngestEventIn, IngestResultOut, IngestSnapshotIn
from .deps import require_ingest_token

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ingest",
    tags=["ingest"],
    dependencies=[Depends(require_ingest_token)],
)


async def _broadcast_if_needed(request: Request, result) -> None:
    if result.changed or result.presence_changed:
        await request.app.state.hub.broadcast(request.app.state.manager.snapshot())


@router.post("/snapshot", response_model=IngestResultOut)
async def ingest_snapshot(payload: IngestSnapshotIn, request: Request) -> IngestResultOut:
    manager = request.app.state.manager
    result = await asyncio.to_thread(manager.apply_snapshot, payload.seats, payload.at)
    await _broadcast_if_needed(request, result)
    if result.events:
        for event in result.events:
            log.info(
                "seat_event %s %s session=%s reason=%s",
                event["action"],
                event["seat"],
                event["session_number"],
                event["reason"],
            )
    return IngestResultOut(
        ok=True,
        changed=result.changed,
        presence_changed=result.presence_changed,
        events=result.events,
        unknown=result.unknown_codes,
        state=manager.snapshot(),
    )


@router.post("/event", response_model=IngestResultOut)
async def ingest_event(payload: IngestEventIn, request: Request) -> IngestResultOut:
    action = (payload.action or "").strip().lower()
    if action not in {"enter", "leave"}:
        raise HTTPException(status_code=422, detail="action must be 'enter' or 'leave'")

    manager = request.app.state.manager
    result = await asyncio.to_thread(
        manager.observe, payload.seat, action == "enter", payload.at
    )
    if result.unknown_codes:
        raise HTTPException(status_code=404, detail=f"Unknown seat: {payload.seat}")
    await _broadcast_if_needed(request, result)
    return IngestResultOut(
        ok=True,
        changed=result.changed,
        presence_changed=result.presence_changed,
        events=result.events,
        unknown=result.unknown_codes,
        state=manager.snapshot(),
    )
