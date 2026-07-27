"""Daily min/max/mean aggregation over raw observations."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable
from zoneinfo import ZoneInfo

import psycopg

from dombori.observations import Observation

logger = logging.getLogger(__name__)

_BUDAPEST_TZ = ZoneInfo("Europe/Budapest")

# Recomputes daily_aggregates directly in the database from `observations`,
# bucketing by the Europe/Budapest calendar day (handles DST transitions
# naturally, since the bucketing itself is done by the database in the same
# timezone).
UPSERT_DAILY_AGGREGATES_SQL = """
    INSERT INTO daily_aggregates
        (station_tsz, day_local, min_cm, max_cm, mean_cm, sample_count, computed_at)
    SELECT
        station_tsz,
        (ts_utc AT TIME ZONE 'Europe/Budapest')::date AS day_local,
        min(value_cm),
        max(value_cm),
        round(avg(value_cm), 1),
        count(*),
        now()
    FROM observations
    WHERE ts_utc >= now() - make_interval(days => %s)
    GROUP BY 1, 2
    ON CONFLICT (station_tsz, day_local) DO UPDATE
        SET min_cm = EXCLUDED.min_cm,
            max_cm = EXCLUDED.max_cm,
            mean_cm = EXCLUDED.mean_cm,
            sample_count = EXCLUDED.sample_count,
            computed_at = now()
"""

_UPSERT_ROW_SQL = """
    INSERT INTO daily_aggregates
        (station_tsz, day_local, min_cm, max_cm, mean_cm, sample_count, computed_at)
    VALUES (%s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (station_tsz, day_local) DO UPDATE
        SET min_cm = EXCLUDED.min_cm,
            max_cm = EXCLUDED.max_cm,
            mean_cm = EXCLUDED.mean_cm,
            sample_count = EXCLUDED.sample_count,
            computed_at = now()
"""


@dataclass(frozen=True)
class DailyAggregate:
    station_tsz: int
    day_local: date
    min_cm: float
    max_cm: float
    mean_cm: float
    sample_count: int


def upsert_daily_aggregates(conn: psycopg.Connection, lookback_days: int = 35) -> int:
    """Recompute daily_aggregates for the last `lookback_days` in-database.

    Returns the number of (station, day) rows written. Caller commits.
    """
    with conn.cursor() as cur:
        cur.execute(UPSERT_DAILY_AGGREGATES_SQL, (lookback_days,))
        rowcount = cur.rowcount
    conn.commit()
    logger.info("Upserted %d daily aggregate rows (lookback=%dd)", rowcount, lookback_days)
    return rowcount


def aggregate_daily(observations: Iterable[Observation]) -> tuple[DailyAggregate, ...]:
    """Bucket observations by (station, local calendar day) and summarize.

    Pure function -- no I/O. Buckets by the Europe/Budapest local date of
    each observation's UTC timestamp, which handles sparse historical data
    (1-2 points/day) and DST transitions correctly: each observation lands
    in exactly the calendar day its local wall-clock time falls on.
    """
    buckets: dict[tuple[int, date], list[float]] = defaultdict(list)
    for obs in observations:
        local_day = obs.ts_utc.astimezone(_BUDAPEST_TZ).date()
        buckets[(obs.station_tsz, local_day)].append(obs.value_cm)

    rows = []
    for (station_tsz, day_local), values in buckets.items():
        mean_cm = round(sum(values) / len(values), 1)
        rows.append(
            DailyAggregate(
                station_tsz=station_tsz,
                day_local=day_local,
                min_cm=min(values),
                max_cm=max(values),
                mean_cm=mean_cm,
                sample_count=len(values),
            )
        )
    # Deterministic ordering makes this pure function's output easy to test.
    rows.sort(key=lambda r: (r.station_tsz, r.day_local))
    return tuple(rows)


def upsert_daily_rows(conn: psycopg.Connection, rows: tuple[DailyAggregate, ...]) -> int:
    """Upsert pre-computed DailyAggregate rows. Caller commits."""
    if not rows:
        return 0
    params = [
        (r.station_tsz, r.day_local, r.min_cm, r.max_cm, r.mean_cm, r.sample_count)
        for r in rows
    ]
    with conn.cursor() as cur:
        cur.executemany(_UPSERT_ROW_SQL, params)
        return max(cur.rowcount, 0)
