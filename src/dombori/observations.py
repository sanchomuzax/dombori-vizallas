"""Parsing and persistence for raw water-level observations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import psycopg

logger = logging.getLogger(__name__)


class ParseError(ValueError):
    """Raised when a TsShortList payload is structurally invalid."""


@dataclass(frozen=True)
class Observation:
    station_tsz: int
    ts_utc: datetime
    value_cm: float


def _parse_utc_time(raw: str) -> datetime:
    # "2026-07-25T22:00:00Z" -> aware UTC datetime.
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def parse_timeseries(payload: list[dict]) -> tuple[Observation, ...]:
    """Parse a TsShortList response into a flat tuple of Observations.

    Entries with a null or non-numeric "Adat" value are skipped (logged at
    debug) rather than raising -- gaps are expected in real telemetry.
    Structural problems (wrong shape, missing keys) raise ParseError.
    """
    if not isinstance(payload, list):
        raise ParseError(f"Expected a list payload, got {type(payload)}")

    observations: list[Observation] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ParseError(f"Expected dict items, got {type(item)}")
        if "ItemId" not in item or "TsItemList" not in item:
            raise ParseError(f"Item missing ItemId/TsItemList: {item!r}")

        try:
            station_tsz = int(item["ItemId"])
        except (TypeError, ValueError) as exc:
            raise ParseError(f"Invalid ItemId {item.get('ItemId')!r}: {exc}") from exc

        ts_items = item["TsItemList"]
        if not isinstance(ts_items, list):
            raise ParseError(f"TsItemList must be a list for station {station_tsz}")

        for ts_item in ts_items:
            if not isinstance(ts_item, dict) or "UTCTime" not in ts_item:
                raise ParseError(
                    f"TsItemList entry missing UTCTime for station {station_tsz}: {ts_item!r}"
                )

            adat = ts_item.get("Adat")
            if adat is None:
                logger.debug(
                    "Skipping null Adat for station %s at %s",
                    station_tsz,
                    ts_item.get("UTCTime"),
                )
                continue
            try:
                value_cm = float(adat)
            except (TypeError, ValueError):
                logger.debug(
                    "Skipping non-numeric Adat=%r for station %s at %s",
                    adat,
                    station_tsz,
                    ts_item.get("UTCTime"),
                )
                continue

            try:
                ts_utc = _parse_utc_time(ts_item["UTCTime"])
            except ValueError as exc:
                raise ParseError(
                    f"Invalid UTCTime {ts_item['UTCTime']!r} for station {station_tsz}: {exc}"
                ) from exc

            observations.append(Observation(station_tsz, ts_utc, value_cm))

    return tuple(observations)


_UPSERT_SQL = """
    INSERT INTO observations (station_tsz, ts_utc, value_cm)
    VALUES (%s, %s, %s)
    ON CONFLICT (station_tsz, ts_utc) DO UPDATE
        SET value_cm = EXCLUDED.value_cm, updated_at = now()
        WHERE observations.value_cm IS DISTINCT FROM EXCLUDED.value_cm
"""


def upsert_observations(
    conn: psycopg.Connection, observations: tuple[Observation, ...]
) -> int:
    """Upsert observations. Caller is responsible for commit/rollback.

    Returns the number of rows actually inserted or updated: psycopg 3
    aggregates ``rowcount`` across an executemany() batch, and rows skipped
    by the ON CONFLICT ... WHERE guard (unchanged values) are not counted.
    """
    if not observations:
        return 0

    params = [(o.station_tsz, o.ts_utc, o.value_cm) for o in observations]
    with conn.cursor() as cur:
        cur.executemany(_UPSERT_SQL, params)
        return max(cur.rowcount, 0)
