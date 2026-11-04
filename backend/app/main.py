"""FastAPI application entry point.

Run from the repository root:

    uvicorn backend.app.main:app --reload
or
    python -m uvicorn app.main:app --reload   (from the backend/ directory)

The app also serves the existing Three.js frontend, so opening
http://127.0.0.1:8000/ starts the complete application.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import ingest, occupancy, seats, sessions, statistics
from .config import REPO_ROOT, settings
from .db import SessionLocal, engine
from .db_init import init_database
from .services.occupancy_manager import OccupancyManager
from .services.session_manager import SessionManager
from .ws import ConnectionHub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("twin")

hub = ConnectionHub()


async def _boundary_loop(app: FastAPI) -> None:
    """Periodically move occupancy across session boundaries."""
    interval = max(0.2, float(settings.boundary_tick_seconds))
    while True:
        await asyncio.sleep(interval)
        try:
            manager: OccupancyManager = app.state.manager
            result = await asyncio.to_thread(manager.tick)
            if result.changed or result.presence_changed:
                await app.state.hub.broadcast(manager.snapshot())
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("Session boundary tick failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_init_db:
        init_database(engine)

    with SessionLocal() as db:
        session_manager = SessionManager.load(db)

    manager = OccupancyManager(
        SessionLocal,
        session_manager,
        occupied_confirm_seconds=settings.occupied_confirm_seconds,
        empty_confirm_seconds=settings.empty_confirm_seconds,
        presence_grace_seconds=settings.presence_grace_seconds,
    )

    app.state.manager = manager
    app.state.hub = hub
    task = asyncio.create_task(_boundary_loop(app))

    log.info(
        "Digital Twin ready | %d seats | %d sessions | timezone=%s | confirm=%.1fs/%.1fs",
        len(manager.seats),
        len(session_manager.windows),
        settings.timezone,
        settings.occupied_confirm_seconds,
        settings.empty_confirm_seconds,
    )
    if manager.recovered_seats:
        log.info("Recovered seats: %s", ", ".join(manager.recovered_seats))

    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log.info("Digital Twin stopped")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Seat occupancy backend for the 3D classroom digital twin.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api"
app.include_router(seats.router, prefix=API_PREFIX)
app.include_router(sessions.router, prefix=API_PREFIX)
app.include_router(occupancy.router, prefix=API_PREFIX)
app.include_router(statistics.router, prefix=API_PREFIX)
app.include_router(ingest.router, prefix=API_PREFIX)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    database_ok = True
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
    except Exception:
        database_ok = False
    return {
        "status": "ok" if database_ok else "degraded",
        "database": database_ok,
        "ws_clients": hub.client_count,
        "config": settings.public_dict(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        await websocket.send_json(app.state.manager.snapshot())
        while True:
            message = await websocket.receive_text()
            if message.strip().lower() in {"ping", '{"type":"ping"}'}:
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("WebSocket error")
    finally:
        await hub.disconnect(websocket)


if settings.serve_frontend:
    FRONTEND_DIR = REPO_ROOT / "frontend"
    # The image is rebuilt when the frontend changes. Force revalidation so a
    # browser never keeps an old digital-twin.js / backend-client.js after a
    # rebuild and renders a stale UI.
    NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

    @app.get("/", include_in_schema=False)
    def serve_index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html", headers=NO_CACHE)

    @app.get("/digital-twin.js", include_in_schema=False)
    def serve_digital_twin() -> FileResponse:
        return FileResponse(
            FRONTEND_DIR / "digital-twin.js",
            media_type="application/javascript",
            headers=NO_CACHE,
        )

    @app.get("/backend-client.js", include_in_schema=False)
    def serve_backend_client() -> FileResponse:
        return FileResponse(
            FRONTEND_DIR / "backend-client.js",
            media_type="application/javascript",
            headers=NO_CACHE,
        )
