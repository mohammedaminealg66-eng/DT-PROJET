#!/usr/bin/env python3
"""Create the schema and seed the 24 seats + 4 sessions.

Usage (from the repository root):

    python backend/scripts/init_db.py
    python backend/scripts/init_db.py --database-url postgresql+psycopg://twin:twin@127.0.0.1:5432/classroom_twin

The script is idempotent: running it again does not duplicate anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db_init import init_database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize the digital twin database")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="SQLAlchemy URL (default: DATABASE_URL from .env)",
    )
    args = parser.parse_args()

    engine = create_engine(args.database_url, future=True, pool_pre_ping=True)
    init_database(engine)

    with engine.connect() as conn:
        seats = conn.scalar(text("SELECT count(*) FROM seats"))
        sessions = conn.scalar(text("SELECT count(*) FROM sessions"))
        open_records = conn.scalar(
            text("SELECT count(*) FROM occupancy_records WHERE occupied_until IS NULL")
        )

    print(f"[init_db] schema ready: {seats} seats, {sessions} sessions, "
          f"{open_records} open occupancy record(s)")
    print(f"[init_db] database: {args.database_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
