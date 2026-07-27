"""Resumable historical backfill of raw observations via TsShortList.

Walks each station's history backwards in fixed-size (`chunk_years`) time
windows, persisting recent points as raw observations and older points
(more than 28 days old) as daily aggregates only -- there's no value in
keeping raw 15-minute readings from decades ago once they're summarized.
Progress is tracked per-station in `backfill_state` so a later run resumes
where the previous one left off (or skips a station once its `done` flag
is set).
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
import requests

from dombori import runs
from dombori.aggregate import aggregate_daily, upsert_daily_rows
from dombori.config import Config
from dombori.observations import Observation, parse_timeseries, upsert_observations
from dombori.vizugy_client import fetch_timeseries, fetch_token, token_expiry

logger = logging.getLogger(__name__)

_BUDAPEST_TZ = ZoneInfo("Europe/Budapest")
_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)
_AGGREGATE_CUTOFF_DAYS = 28
# Defensive upper bound on chunks processed per station in one call, so a
# data source that never runs dry can't spin this forever (200 * 10y chunks
# = 2000 years, far beyond any plausible gauge record).
_MAX_CHUNKS_PER_STATION = 200


class BackfillError(RuntimeError):
    """Raised when a backfill chunk fails unrecoverably."""


def _get_backfill_state(conn: psycopg.Connection, station_tsz: int) -> tuple[date, bool] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT next_end_date, done FROM backfill_state WHERE station_tsz = %s",
            (station_tsz,),
        )
        row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _min_observation_date(conn: psycopg.Connection, station_tsz: int) -> date | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT min(ts_utc) FROM observations WHERE station_tsz = %s", (station_tsz,)
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0].astimezone(_BUDAPEST_TZ).date()


def _upsert_backfill_state(
    conn: psycopg.Connection, station_tsz: int, next_end_date: date, done: bool
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO backfill_state (station_tsz, next_end_date, done, updated_at) "
            "VALUES (%s, %s, %s, now()) "
            "ON CONFLICT (station_tsz) DO UPDATE "
            "SET next_end_date = EXCLUDED.next_end_date, done = EXCLUDED.done, updated_at = now()",
            (station_tsz, next_end_date, done),
        )
    conn.commit()


def _ensure_token(
    session: requests.Session, token: str | None, expiry: datetime | None
) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    if token is None or expiry is None or (expiry - now) < _TOKEN_REFRESH_MARGIN:
        token = fetch_token(session)
        expiry = token_expiry(token)
        logger.debug("Refreshed vizugy token, expires %s", expiry.isoformat())
    return token, expiry


def _split_observations(
    observations: tuple[Observation, ...], cutoff_utc: datetime
) -> tuple[tuple[Observation, ...], tuple[Observation, ...]]:
    """Split by ts_utc vs cutoff into (recent, historical)."""
    recent = tuple(o for o in observations if o.ts_utc >= cutoff_utc)
    historical = tuple(o for o in observations if o.ts_utc < cutoff_utc)
    return recent, historical


def _process_chunk(
    conn: psycopg.Connection,
    session: requests.Session,
    station_tsz: int,
    token: str,
    chunk_start_date: date,
    chunk_end_date: date,
) -> int:
    """Fetch, split, and persist one chunk. Returns the point count.
    Commits on success; rolls back and raises BackfillError on failure.
    """
    start_local = datetime.combine(chunk_start_date, datetime.min.time())
    end_local = datetime.combine(chunk_end_date, datetime.min.time())
    cutoff_utc = datetime.now(timezone.utc) - timedelta(days=_AGGREGATE_CUTOFF_DAYS)

    try:
        payload = fetch_timeseries(session, token, [station_tsz], start_local, end_local)
        observations = parse_timeseries(payload)

        recent, historical = _split_observations(observations, cutoff_utc)
        if recent:
            upsert_observations(conn, recent)
        if historical:
            upsert_daily_rows(conn, aggregate_daily(historical))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise BackfillError(
            f"Backfill chunk failed for station {station_tsz} "
            f"[{chunk_start_date} .. {chunk_end_date}]: {exc}"
        ) from exc

    return len(observations)


def _run_station_backfill(
    conn: psycopg.Connection,
    session: requests.Session,
    station_tsz: int,
    chunk_years: int,
    pause_seconds: float,
    token: str | None,
    expiry: datetime | None,
) -> tuple[str | None, datetime | None]:
    state = _get_backfill_state(conn, station_tsz)
    if state is not None and state[1]:
        logger.info("Station %s backfill already done, skipping", station_tsz)
        return token, expiry

    chunk_end_date = (
        state[0]
        if state is not None
        else _min_observation_date(conn, station_tsz) or datetime.now(_BUDAPEST_TZ).date()
    )
    consecutive_empty = 0

    for _ in range(_MAX_CHUNKS_PER_STATION):
        chunk_start_date = chunk_end_date - timedelta(days=365 * chunk_years)

        run_id = runs.start_run(conn, "backfill")
        token, expiry = _ensure_token(session, token, expiry)

        try:
            point_count = _process_chunk(
                conn, session, station_tsz, token, chunk_start_date, chunk_end_date
            )
        except BackfillError as exc:
            runs.finish_run(
                conn,
                run_id,
                "error",
                detail={
                    "station": station_tsz,
                    "span": [chunk_start_date.isoformat(), chunk_end_date.isoformat()],
                    "error": str(exc),
                },
            )
            raise

        runs.finish_run(
            conn,
            run_id,
            "ok" if point_count else "noop",
            rows_upserted=point_count,
            detail={
                "station": station_tsz,
                "span": [chunk_start_date.isoformat(), chunk_end_date.isoformat()],
                "points": point_count,
            },
        )

        consecutive_empty = consecutive_empty + 1 if point_count == 0 else 0
        done = consecutive_empty >= 2
        _upsert_backfill_state(conn, station_tsz, chunk_start_date, done)

        if done:
            logger.info(
                "Station %s backfill complete (reached %s)", station_tsz, chunk_start_date
            )
            break

        chunk_end_date = chunk_start_date
        time.sleep(pause_seconds)
    else:
        logger.warning(
            "Station %s hit the chunk safety cap (%d) without completing backfill",
            station_tsz,
            _MAX_CHUNKS_PER_STATION,
        )

    return token, expiry


def run_backfill(
    conn: psycopg.Connection,
    config: Config,
    tsz_list: tuple[int, ...] = (550, 142062),
    chunk_years: int = 10,
    pause_seconds: float = 3.0,
) -> None:
    """Backfill history for each station in `tsz_list`, resuming from
    `backfill_state` where previous runs left off. `config` is accepted for
    interface symmetry with the other job entry points (unused directly:
    all state lives in the database).
    """
    del config  # unused: kept for a consistent job-function signature
    session = requests.Session()
    token: str | None = None
    expiry: datetime | None = None

    for station_tsz in tsz_list:
        token, expiry = _run_station_backfill(
            conn, session, station_tsz, chunk_years, pause_seconds, token, expiry
        )
