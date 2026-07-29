"""Statisztikai 6 napos előrejelzés a Bartal (142062) állomásra.

Módszer: perzisztencia + szezonális drift + empirikus hibasáv.
A 2005 utáni napi átlagokból, az aktuális naptári nap +/- ablakában mért
k napos történelmi változások mediánja adja a driftet, p5-p95 terjedelme
a ~90%-os hibasávot. A Duna-korreláció bizonyítottan elhanyagolható
(delta-korreláció ~0), ezért a Duna csak eseményjelzőként szerepel:
ha a Hydroinfo Duna-előrejelzés eléri a 400 cm-t (szivornya-minimum),
a run detail mezőjében 'szivornya_lehetseges' = true.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

import psycopg

logger = logging.getLogger(__name__)

BARTAL_TSZ = 142062
STATION_CODE = "142062"
SOURCE = "statisztikai"
HORIZON_DAYS = 6
HISTORY_START = date(2005, 1, 1)
DOY_WINDOW = 10          # +/- napok a szezonális mintavételhez
MIN_SAMPLES = 100        # ennyi alatt szélesebb ablak
WIDE_DOY_WINDOW = 21
SIPHON_MIN_CM = 400.0
TZ = ZoneInfo("Europe/Budapest")


class ForecastError(Exception):
    """A statisztikai előrejelzés nem állítható elő."""


@dataclass(frozen=True)
class StatPoint:
    target_ts: datetime
    value_cm: float
    error_band_cm: float


def _doy_distance(d1: int, d2: int) -> int:
    """Naptári nap-távolság évforduló-átfordulással."""
    diff = abs(d1 - d2)
    return min(diff, 366 - diff)


def _k_day_changes(
    series: tuple[tuple[date, float], ...], anchor_doy: int, k: int, window: int
) -> tuple[float, ...]:
    """Történelmi k napos változások, ahol a kiindulónap az anchor +/- window."""
    by_date = dict(series)
    changes = []
    for day, value in series:
        if _doy_distance(day.timetuple().tm_yday, anchor_doy) > window:
            continue
        later = by_date.get(day + timedelta(days=k))
        if later is not None:
            changes.append(later - value)
    return tuple(changes)


def _percentile(sorted_values: tuple[float, ...], q: float) -> float:
    """Lineárisan interpolált percentilis (0..1)."""
    if not sorted_values:
        raise ForecastError("Üres minta a percentilishez")
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def compute_forecast(
    series: tuple[tuple[date, float], ...],
    last_day: date,
    last_value: float,
    horizon: int = HORIZON_DAYS,
) -> tuple[StatPoint, ...]:
    """Pure: perzisztencia + szezonális drift + empirikus sáv pontonként."""
    if len(series) < 365:
        raise ForecastError(f"Túl rövid történelmi sor: {len(series)} nap")

    anchor_doy = last_day.timetuple().tm_yday
    points = []
    for k in range(1, horizon + 1):
        changes = _k_day_changes(series, anchor_doy, k, DOY_WINDOW)
        if len(changes) < MIN_SAMPLES:
            changes = _k_day_changes(series, anchor_doy, k, WIDE_DOY_WINDOW)
        if len(changes) < MIN_SAMPLES:
            raise ForecastError(
                f"Kevés minta a {k} napos horizonthoz: {len(changes)}"
            )
        ordered = tuple(sorted(changes))
        drift = median(ordered)
        band = (_percentile(ordered, 0.95) - _percentile(ordered, 0.05)) / 2.0
        target = datetime.combine(last_day + timedelta(days=k), time(12, 0), TZ)
        points.append(
            StatPoint(
                target_ts=target,
                value_cm=round(last_value + drift, 1),
                error_band_cm=round(max(band, 0.5), 1),
            )
        )
    return tuple(points)


def expand_to_6h_grid(
    daily_points: tuple[StatPoint, ...], last_day: date, last_value: float
) -> tuple[StatPoint, ...]:
    """Pure: a napi pontok lineáris interpolációja 6 órás rácsra.

    A kiindulópont (k=0) az utolsó mért napi átlag (dél), drift és sáv nulla;
    így a kimenet ugyanolyan sűrű, mint a Hydroinfo 6 órás előrejelzése.
    """
    anchor = datetime.combine(last_day, time(12, 0), TZ)
    knots = [(0.0, last_value, 0.0)] + [
        (float(i + 1), p.value_cm, p.error_band_cm) for i, p in enumerate(daily_points)
    ]
    grid = []
    steps_per_day = 4
    for step in range(1, len(daily_points) * steps_per_day + 1):
        k = step / steps_per_day
        lo = min((step - 1) // steps_per_day, len(knots) - 2)
        frac = k - lo
        _, v0, e0 = knots[lo]
        _, v1, e1 = knots[lo + 1]
        grid.append(
            StatPoint(
                target_ts=anchor + timedelta(hours=6 * step),
                value_cm=round(v0 + (v1 - v0) * frac, 1),
                error_band_cm=round(max(e0 + (e1 - e0) * frac, 0.5), 1),
            )
        )
    return tuple(grid)


def _load_series(conn: psycopg.Connection) -> tuple[tuple[date, float], ...]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT day_local, mean_cm::float FROM daily_aggregates"
            " WHERE station_tsz = %s AND day_local >= %s ORDER BY day_local",
            (BARTAL_TSZ, HISTORY_START),
        )
        return tuple((row[0], row[1]) for row in cur.fetchall())


def _duna_forecast_max(conn: psycopg.Connection) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(value_cm)::float FROM forecast_points WHERE point_type='forecast'"
            " AND run_id = (SELECT id FROM forecast_runs WHERE station_code='442540H'"
            "               AND source IN ('table','imagemap') ORDER BY issue_ts DESC LIMIT 1)"
        )
        row = cur.fetchone()
        return row[0] if row else None


def run_bartal_forecast(conn: psycopg.Connection, config) -> int | None:
    """Számol, tárol; a run id-t adja vissza (None, ha nincs elég adat)."""
    series = _load_series(conn)
    if not series:
        raise ForecastError("Nincs Bartal napi adat")
    last_day, last_value = series[-1]
    daily_points = compute_forecast(series, last_day, last_value)
    points = expand_to_6h_grid(daily_points, last_day, last_value)

    duna_max = _duna_forecast_max(conn)
    siphon_possible = duna_max is not None and duna_max >= SIPHON_MIN_CM
    issue_ts = datetime.now(timezone.utc)

    payload = {
        "modszer": "perzisztencia + szezonalis drift (median) + p5-p95 sav",
        "tortenelmi_kezdet": HISTORY_START.isoformat(),
        "utolso_nap": last_day.isoformat(),
        "utolso_ertek_cm": last_value,
        "duna_forecast_max_cm": duna_max,
        "szivornya_lehetseges": siphon_possible,
        "pontok": [
            {"target": p.target_ts.isoformat(), "ertek": p.value_cm, "sav": p.error_band_cm}
            for p in points
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    content_hash = hashlib.sha256(raw).hexdigest()

    raw_dir = config.data_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_name = f"{STATION_CODE}S-{issue_ts:%Y%m%dT%H%M}-{content_hash[:8]}.json.gz"
    with gzip.open(raw_dir / raw_name, "wb") as fh:
        fh.write(raw)

    detail = {
        "duna_forecast_max_cm": duna_max,
        "szivornya_lehetseges": siphon_possible,
        "utolso_nap": last_day.isoformat(),
        "utolso_ertek_cm": last_value,
    }
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO forecast_runs (station_code, issue_ts, content_hash, source,"
            " raw_path, detail) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (station_code, content_hash) DO NOTHING RETURNING id",
            (STATION_CODE, issue_ts, content_hash, SOURCE, f"raw/{raw_name}",
             json.dumps(detail)),
        )
        row = cur.fetchone()
        if row is None:
            logger.info("Bartal forecast: azonos tartalmú run már létezik (noop)")
            return None
        run_id = row[0]
        params = [
            (run_id, datetime.combine(last_day, time(12, 0), TZ), last_value, None, "observed")
        ] + [(run_id, p.target_ts, p.value_cm, p.error_band_cm, "forecast") for p in points]
        cur.executemany(
            "INSERT INTO forecast_points (run_id, target_ts, value_cm, error_band_cm,"
            " point_type) VALUES (%s, %s, %s, %s, %s)",
            params,
        )
    conn.commit()
    logger.info(
        "Bartal forecast run id=%s: %s pont, szivornya_lehetseges=%s",
        run_id, len(points), siphon_possible,
    )
    return run_id
