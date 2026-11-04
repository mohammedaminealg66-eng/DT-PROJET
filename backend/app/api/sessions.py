"""Session endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import Session as SessionModel
from ..schemas import SessionContextOut, SessionOut

router = APIRouter(tags=["sessions"])


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: OrmSession = Depends(get_db)) -> list[SessionOut]:
    """The four recurring daily sessions."""
    return list(
        db.scalars(
            select(SessionModel).order_by(SessionModel.session_number)
        ).all()
    )


@router.get("/sessions/current", response_model=SessionContextOut)
def current_session(request: Request) -> SessionContextOut:
    """Current session (Africa/Casablanca), or null during a break."""
    manager = request.app.state.manager
    return SessionContextOut(**manager.session_context())
