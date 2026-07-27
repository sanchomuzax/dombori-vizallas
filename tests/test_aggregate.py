"""Unit tests for the pure aggregate_daily function in dombori.aggregate.

No database, no I/O -- aggregate_daily is a plain in-memory bucketing
function over Observation instances.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from dombori.aggregate import DailyAggregate, aggregate_daily
from dombori.observations import Observation


def _obs(station_tsz: int, iso_utc: str, value_cm: float) -> Observation:
    return Observation(
        station_tsz=station_tsz,
        ts_utc=datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc),
        value_cm=value_cm,
    )


# --------------------------------------------------------------------------
# Normal dense day
# --------------------------------------------------------------------------


def test_aggregate_daily_dense_day():
    observations = [
        _obs(550, "2026-07-15T04:00:00", -87.0),
        _obs(550, "2026-07-15T08:00:00", -89.0),
        _obs(550, "2026-07-15T12:00:00", -90.0),
        _obs(550, "2026-07-15T16:00:00", -88.0),
        _obs(550, "2026-07-15T20:00:00", -86.0),
    ]
    rows = aggregate_daily(observations)

    assert len(rows) == 1
    row = rows[0]
    assert row.station_tsz == 550
    assert row.day_local == date(2026, 7, 15)
    assert row.min_cm == -90.0
    assert row.max_cm == -86.0
    assert row.sample_count == 5
    assert row.mean_cm == round((-87.0 - 89.0 - 90.0 - 88.0 - 86.0) / 5, 1)


# --------------------------------------------------------------------------
# Sparse historical day (2 points/day, as real vizugy backfill data looks)
# --------------------------------------------------------------------------


def test_aggregate_daily_sparse_historical_day():
    observations = [
        _obs(550, "2005-01-01T06:00:00", 90.0),
        _obs(550, "2005-01-01T15:00:00", 89.0),
    ]
    rows = aggregate_daily(observations)

    assert len(rows) == 1
    row = rows[0]
    assert row.day_local == date(2005, 1, 1)
    assert row.min_cm == 89.0
    assert row.max_cm == 90.0
    assert row.mean_cm == 89.5
    assert row.sample_count == 2


# --------------------------------------------------------------------------
# UTC -> local-day bucketing across midnight (July, CEST = UTC+2)
# --------------------------------------------------------------------------


def test_aggregate_daily_utc_midnight_crossover_july():
    # 22:30 UTC in July is 00:30 local (CEST, +2h) -- belongs to the NEXT
    # local calendar day, not the UTC day.
    observations = [
        _obs(550, "2026-07-15T22:30:00", -80.0),  # -> local 2026-07-16 00:30
        _obs(550, "2026-07-16T10:00:00", -81.0),  # -> local 2026-07-16 12:00
    ]
    rows = aggregate_daily(observations)

    assert len(rows) == 1
    assert rows[0].day_local == date(2026, 7, 16)
    assert rows[0].sample_count == 2


def test_aggregate_daily_utc_evening_point_stays_same_local_day():
    # 20:00 UTC in July is 22:00 local -- still the same calendar day.
    observations = [_obs(550, "2026-07-15T20:00:00", -80.0)]
    rows = aggregate_daily(observations)

    assert len(rows) == 1
    assert rows[0].day_local == date(2026, 7, 15)


# --------------------------------------------------------------------------
# DST transition days (Europe/Budapest, 2026): spring 03-29, autumn 10-25
# --------------------------------------------------------------------------


def test_aggregate_daily_spring_dst_transition():
    observations = [
        _obs(550, "2026-03-28T22:00:00", -70.0),  # -> local 2026-03-28 23:00 CET
        _obs(550, "2026-03-29T00:30:00", -71.0),  # -> local 2026-03-29 01:30 CET
        _obs(550, "2026-03-29T01:30:00", -72.0),  # -> local 2026-03-29 03:30 CEST
        _obs(550, "2026-03-29T22:30:00", -73.0),  # -> local 2026-03-30 00:30 CEST
    ]
    rows = aggregate_daily(observations)
    by_day = {r.day_local: r for r in rows}

    assert set(by_day) == {date(2026, 3, 28), date(2026, 3, 29), date(2026, 3, 30)}
    assert by_day[date(2026, 3, 28)].sample_count == 1
    assert by_day[date(2026, 3, 29)].sample_count == 2
    assert by_day[date(2026, 3, 30)].sample_count == 1


def test_aggregate_daily_autumn_dst_transition():
    observations = [
        _obs(550, "2026-10-24T21:00:00", -70.0),  # -> local 2026-10-24 23:00 CEST
        _obs(550, "2026-10-24T22:30:00", -71.0),  # -> local 2026-10-25 00:30 CEST
        _obs(550, "2026-10-25T00:30:00", -72.0),  # -> local 2026-10-25 02:30 CEST
        _obs(550, "2026-10-25T23:30:00", -73.0),  # -> local 2026-10-26 00:30 CET
    ]
    rows = aggregate_daily(observations)
    by_day = {r.day_local: r for r in rows}

    assert set(by_day) == {date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26)}
    assert by_day[date(2026, 10, 24)].sample_count == 1
    assert by_day[date(2026, 10, 25)].sample_count == 2
    assert by_day[date(2026, 10, 26)].sample_count == 1


# --------------------------------------------------------------------------
# Mean rounding to 1 decimal
# --------------------------------------------------------------------------


def test_aggregate_daily_mean_rounds_to_one_decimal():
    observations = [
        _obs(550, "2026-07-15T04:00:00", 1.0),
        _obs(550, "2026-07-15T08:00:00", 2.0),
        _obs(550, "2026-07-15T12:00:00", 2.0),
    ]
    rows = aggregate_daily(observations)

    assert rows[0].mean_cm == 1.7  # 5/3 = 1.6666... -> round(_, 1) == 1.7


# --------------------------------------------------------------------------
# Multiple stations kept separate
# --------------------------------------------------------------------------


def test_aggregate_daily_multiple_stations_kept_separate():
    observations = [
        _obs(550, "2026-07-15T06:00:00", -87.0),
        _obs(550, "2026-07-15T18:00:00", -89.0),
        _obs(142062, "2026-07-15T06:00:00", 127.0),
        _obs(142062, "2026-07-15T18:00:00", 128.0),
    ]
    rows = aggregate_daily(observations)

    assert len(rows) == 2
    by_station = {r.station_tsz: r for r in rows}
    assert by_station[550].mean_cm == -88.0
    assert by_station[142062].mean_cm == 127.5
    # Deterministic ordering: sorted by (station_tsz, day_local).
    assert [r.station_tsz for r in rows] == [550, 142062]


def test_aggregate_daily_result_rows_are_daily_aggregate_instances():
    observations = [_obs(550, "2026-07-15T06:00:00", -87.0)]
    rows = aggregate_daily(observations)
    assert isinstance(rows[0], DailyAggregate)


# --------------------------------------------------------------------------
# Immutability: input is never mutated
# --------------------------------------------------------------------------


def test_aggregate_daily_does_not_mutate_input():
    observations = [
        _obs(550, "2026-07-15T06:00:00", -87.0),
        _obs(550, "2026-07-15T18:00:00", -89.0),
        _obs(142062, "2026-07-15T06:00:00", 127.0),
    ]
    original = list(observations)

    aggregate_daily(observations)

    assert observations == original
    assert observations[0].value_cm == -87.0
    assert observations[1].value_cm == -89.0
    assert observations[2].value_cm == 127.0


def test_aggregate_daily_returns_new_tuple_not_input_list():
    observations = [_obs(550, "2026-07-15T06:00:00", -87.0)]
    rows = aggregate_daily(observations)
    assert isinstance(rows, tuple)
    assert rows is not observations
