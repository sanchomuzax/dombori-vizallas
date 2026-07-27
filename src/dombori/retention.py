"""Pruning of raw observations once they're safely summarized.

A day's raw observations are only ever deleted once a daily_aggregates row
for that (station, day) exists *and* is newer than every observation it
covers -- the JOIN below is what guarantees a day with no aggregate row is
never touched. Don't relax that condition.
"""

from __future__ import annotations

import logging

import psycopg

logger = logging.getLogger(__name__)

_PRUNE_SQL = """
    DELETE FROM observations o
    USING daily_aggregates d
    WHERE d.station_tsz = o.station_tsz
      AND d.day_local = (o.ts_utc AT TIME ZONE 'Europe/Budapest')::date
      AND d.day_local < ((now() AT TIME ZONE 'Europe/Budapest')::date - %s)
      AND d.computed_at > o.updated_at
"""

_PRUNE_COUNT_SQL = """
    SELECT count(*)
    FROM observations o
    JOIN daily_aggregates d
      ON d.station_tsz = o.station_tsz
     AND d.day_local = (o.ts_utc AT TIME ZONE 'Europe/Budapest')::date
    WHERE d.day_local < ((now() AT TIME ZONE 'Europe/Budapest')::date - %s)
      AND d.computed_at > o.updated_at
"""


def prune_observations(conn: psycopg.Connection, keep_days: int = 28, dry_run: bool = False) -> int:
    """Delete (or, if dry_run, just count) observations already aggregated
    and older than `keep_days`. Returns the affected/matched row count.
    Caller commits (a no-op for dry_run's plain SELECT).
    """
    sql = _PRUNE_COUNT_SQL if dry_run else _PRUNE_SQL
    with conn.cursor() as cur:
        cur.execute(sql, (keep_days,))
        if dry_run:
            row = cur.fetchone()
            count = int(row[0]) if row else 0
        else:
            count = cur.rowcount
    conn.commit()

    logger.info(
        "%s %d observation row(s) older than %d day(s)",
        "Would prune" if dry_run else "Pruned",
        count,
        keep_days,
    )
    return count
