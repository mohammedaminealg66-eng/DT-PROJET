"""Database bootstrap: executes the SQL schema + seed files.

The SQL files are the single source of truth for the deployed schema.
`init_database()` is idempotent and is safe to run on every start.
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.engine import Engine

from .config import settings

log = logging.getLogger(__name__)


def _split_statements(sql: str):
    """Split a SQL script into individual statements.

    The project SQL contains no dollar-quoted functions or semicolons inside
    string literals, so splitting on ';' is sufficient and keeps psycopg
    from having to handle a multi-statement string.
    """
    for chunk in sql.split(";"):
        lines = [line for line in chunk.splitlines() if not line.strip().startswith("--")]
        statement = "\n".join(lines).strip()
        if statement:
            yield statement


def _execute_file(engine: Engine, path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {path}")
    sql = path.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in _split_statements(sql):
            conn.exec_driver_sql(statement)
    log.info("Executed %s", path.name)


def init_database(engine: Engine) -> None:
    """Create tables/indexes and insert the 24 seats + 4 sessions."""
    _execute_file(engine, settings.sql_dir / "001_schema.sql")
    _execute_file(engine, settings.sql_dir / "002_seed.sql")
