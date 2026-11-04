"""Shared pytest fixtures.

Tests run against a REAL PostgreSQL database (no SQLite), because the schema
uses TIMESTAMPTZ and a partial unique index.

Set TEST_DATABASE_URL to point at a disposable database, e.g.:

    $env:TEST_DATABASE_URL = "postgresql+psycopg://twin:twin@127.0.0.1:5432/classroom_twin_test"

The default matches the local development setup described in README.md.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]

# Read .env first so TEST_DATABASE_URL follows the local PostgreSQL setup
# (port included) without duplicating the value in the shell.
load_dotenv(REPO_ROOT / ".env", override=False)

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://twin:twin@127.0.0.1:5432/classroom_twin_test",
)

# Must be set BEFORE importing the application package.
# These are forced (not setdefault) so that a developer .env tuned for real
# detection speeds cannot change the behaviour the tests rely on.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["AUTO_INIT_DB"] = "false"
os.environ["SERVE_FRONTEND"] = "false"
os.environ["OCCUPIED_CONFIRM_SECONDS"] = "0"
os.environ["EMPTY_CONFIRM_SECONDS"] = "0"
os.environ["BOUNDARY_TICK_SECONDS"] = "3600"

TZ = ZoneInfo("Africa/Casablanca")
TEST_DAY = 2  # 2026-11-02


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def set(self, value: datetime) -> None:
        self.value = value


def at(hour: int, minute: int, second: int = 0, day: int = TEST_DAY) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=TZ)


@pytest.fixture(scope="session")
def pg_engine():
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def clean_db(pg_engine):
    with pg_engine.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")

    from app.db_init import init_database

    init_database(pg_engine)
    yield pg_engine


@pytest.fixture()
def session_factory(clean_db):
    return sessionmaker(
        bind=clean_db,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


@pytest.fixture()
def session_manager(session_factory):
    from app.services.session_manager import SessionManager

    with session_factory() as db:
        return SessionManager.load(db)


@pytest.fixture()
def manager(session_factory, session_manager):
    from app.services.occupancy_manager import OccupancyManager

    clock = FakeClock(at(8, 0))
    instance = OccupancyManager(
        session_factory,
        session_manager,
        clock=clock,
        occupied_confirm_seconds=2.0,
        empty_confirm_seconds=3.0,
        presence_grace_seconds=15.0,
    )
    return instance, clock
