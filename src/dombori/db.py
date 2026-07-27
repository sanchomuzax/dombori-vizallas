"""Database connection helpers."""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from dombori.config import Config

logger = logging.getLogger(__name__)


class DatabaseError(RuntimeError):
    """Raised when a database operation fails."""


def connect(config: Config) -> psycopg.Connection:
    """Open a new connection using the given config.

    The returned connection is a context manager (``with connect(cfg) as
    conn: ...``) and defaults to manual commit (``autocommit=False``) so
    callers control transaction boundaries explicitly.
    """
    try:
        return psycopg.connect(
            host=config.db_host,
            port=config.db_port,
            dbname=config.db_name,
            user=config.db_user,
            password=config.db_password,
            autocommit=False,
        )
    except psycopg.OperationalError as exc:
        raise DatabaseError(f"Could not connect to database: {exc}") from exc


def execute_sql_file(conn: psycopg.Connection, path: str | Path) -> None:
    """Execute the full contents of a SQL file in one transaction."""
    sql_path = Path(path)
    try:
        sql_text = sql_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DatabaseError(f"Could not read SQL file {sql_path}: {exc}") from exc

    try:
        with conn.cursor() as cur:
            cur.execute(sql_text)
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        raise DatabaseError(f"Failed executing SQL file {sql_path}: {exc}") from exc
    logger.info("Executed SQL file %s", sql_path)
