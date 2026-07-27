"""Bookkeeping for ingestion_runs rows.

Every job (collect / hydroinfo / daily / backfill) records a start and a
finish row so operators can see what happened without digging through logs.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import psycopg

logger = logging.getLogger(__name__)

Status = Literal["running", "ok", "error", "noop"]

_VALID_STATUSES = {"running", "ok", "error", "noop"}


class RunError(RuntimeError):
    """Raised when ingestion_runs bookkeeping fails."""


def start_run(conn: psycopg.Connection, job: str) -> int:
    """Insert a new ingestion_runs row with status 'running' and commit it.

    Committing immediately (rather than as part of the job's transaction)
    ensures the run is visible even if the job later crashes hard.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ingestion_runs (job, status) VALUES (%s, 'running') "
                "RETURNING id",
                (job,),
            )
            row = cur.fetchone()
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        raise RunError(f"Failed to start run for job {job!r}: {exc}") from exc

    if row is None:
        raise RunError(f"Failed to start run for job {job!r}: no id returned")
    run_id = int(row[0])
    logger.info("Started run id=%s job=%s", run_id, job)
    return run_id


def finish_run(
    conn: psycopg.Connection,
    run_id: int,
    status: Status,
    rows_upserted: int = 0,
    detail: dict[str, Any] | None = None,
) -> None:
    """Mark a run finished, recording status/rows/detail, then commit."""
    if status not in _VALID_STATUSES:
        raise RunError(
            f"Invalid status {status!r}; must be one of {sorted(_VALID_STATUSES)}"
        )

    detail_json = json.dumps(detail or {})
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingestion_runs "
                "SET finished_at = now(), status = %s, rows_upserted = %s, "
                "    detail = %s::jsonb "
                "WHERE id = %s",
                (status, rows_upserted, detail_json, run_id),
            )
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        raise RunError(f"Failed to finish run id={run_id}: {exc}") from exc

    logger.info(
        "Finished run id=%s status=%s rows_upserted=%s", run_id, status, rows_upserted
    )
