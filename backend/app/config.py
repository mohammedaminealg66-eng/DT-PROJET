"""Application configuration, loaded from environment variables / .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/app/config.py -> backend/app -> backend -> repository root
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

# Load .env once, without overriding variables already exported by the shell.
load_dotenv(ENV_FILE, override=False)


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    """Runtime configuration. Everything is overridable via environment variables."""

    def __init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", "Classroom Digital Twin")
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://twin:twin@127.0.0.1:5432/classroom_twin",
        )
        self.timezone = os.getenv("TWIN_TIMEZONE", "Africa/Casablanca")

        # Hysteresis (seconds)
        self.occupied_confirm_seconds = _float("OCCUPIED_CONFIRM_SECONDS", 2.0)
        self.empty_confirm_seconds = _float("EMPTY_CONFIRM_SECONDS", 3.0)
        # How old the last positive detection may be for a seat to be carried
        # across a session boundary (or a restart).
        self.presence_grace_seconds = _float("PRESENCE_GRACE_SECONDS", 15.0)

        self.boundary_tick_seconds = _float("BOUNDARY_TICK_SECONDS", 1.0)
        self.auto_init_db = _bool("AUTO_INIT_DB", True)
        self.serve_frontend = _bool("SERVE_FRONTEND", True)

        self.ingest_token = (os.getenv("TWIN_INGEST_TOKEN") or "").strip() or None

        origins = os.getenv("CORS_ORIGINS", "*")
        self.cors_origins = [o.strip() for o in origins.split(",") if o.strip()] or ["*"]

        self.sql_dir = Path(__file__).resolve().parents[1] / "sql"

    def public_dict(self) -> dict:
        """Configuration that is safe to expose to the frontend."""
        return {
            "app_name": self.app_name,
            "timezone": self.timezone,
            "occupied_confirm_seconds": self.occupied_confirm_seconds,
            "empty_confirm_seconds": self.empty_confirm_seconds,
        }


settings = Settings()
