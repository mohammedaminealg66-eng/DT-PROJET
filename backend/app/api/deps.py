"""Shared API dependencies."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from ..config import settings


def get_manager(request: Request):
    return request.app.state.manager


def get_hub(request: Request):
    return request.app.state.hub


async def require_ingest_token(
    x_ingest_token: str | None = Header(default=None, alias="X-Ingest-Token"),
) -> None:
    """Optional shared secret for the Raspberry Pi ingest endpoints."""
    if settings.ingest_token and x_ingest_token != settings.ingest_token:
        raise HTTPException(status_code=401, detail="Invalid or missing ingest token")
