"""Persistence for hydroinfo.hu forecast snapshots.

Each fetched page is stored twice: gzip-compressed on disk (for audit /
replay) and as structured rows in ``forecast_runs`` / ``forecast_points``.
Snapshots that are byte-identical to an existing one (same content hash)
are treated as a no-op -- hydroinfo often re-serves the same page between
its own update cycles.
"""

from __future__ import annotations

import gzip
import logging
from datetime import datetime
from pathlib import Path

import psycopg

from dombori.config import Config
from dombori.hydroinfo import ForecastPoint, sha256_hex

logger = logging.getLogger(__name__)


class ForecastStoreError(RuntimeError):
    """Raised when persisting a forecast snapshot fails."""


def _existing_run_id(conn: psycopg.Connection, station_code: str, content_hash: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM forecast_runs WHERE station_code = %s AND content_hash = %s",
            (station_code, content_hash),
        )
        row = cur.fetchone()
    return int(row[0]) if row else None


def _write_raw_snapshot(
    data_dir: Path, station_code: str, issue_ts: datetime, content_hash: str, raw: bytes
) -> Path:
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{station_code}-{issue_ts:%Y%m%dT%H%M}-{content_hash[:8]}.html.gz"
    full_path = raw_dir / filename
    try:
        with gzip.open(full_path, "wb") as fh:
            fh.write(raw)
    except OSError as exc:
        raise ForecastStoreError(f"Could not write raw snapshot {full_path}: {exc}") from exc
    return full_path.relative_to(data_dir)


def store_run(
    conn: psycopg.Connection,
    config: Config,
    station_code: str,
    issue_ts: datetime,
    raw: bytes,
    etag: str | None,
    source: str,
    points: tuple[ForecastPoint, ...],
) -> int | None:
    """Store a forecast snapshot. Returns the new run id, or None if a
    run with identical content already exists (caller should log a noop).
    """
    content_hash = sha256_hex(raw)

    existing = _existing_run_id(conn, station_code, content_hash)
    if existing is not None:
        logger.info(
            "Forecast snapshot for %s already stored as run id=%s (unchanged content)",
            station_code,
            existing,
        )
        return None

    relative_path = _write_raw_snapshot(config.data_dir, station_code, issue_ts, content_hash, raw)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_runs "
                "(station_code, issue_ts, content_hash, etag, source, raw_path) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (station_code, issue_ts, content_hash, etag, source, str(relative_path)),
            )
            row = cur.fetchone()
            if row is None:
                raise ForecastStoreError("forecast_runs insert did not return an id")
            run_id = int(row[0])

            point_params = [
                (run_id, p.target_ts, p.value_cm, p.error_band_cm, p.point_type)
                for p in points
            ]
            cur.executemany(
                "INSERT INTO forecast_points "
                "(run_id, target_ts, value_cm, error_band_cm, point_type) "
                "VALUES (%s, %s, %s, %s, %s)",
                point_params,
            )
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        raise ForecastStoreError(f"Failed to store forecast run for {station_code}: {exc}") from exc

    logger.info(
        "Stored forecast run id=%s station=%s source=%s points=%d",
        run_id,
        station_code,
        source,
        len(points),
    )
    return run_id


def latest_etag(conn: psycopg.Connection, station_code: str) -> str | None:
    """Return the ETag of the most recently fetched run for a station."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT etag FROM forecast_runs WHERE station_code = %s "
            "ORDER BY issue_ts DESC LIMIT 1",
            (station_code,),
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None
