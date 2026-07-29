"""A statisztikai Bartal-előrejelzés pure függvényeinek tesztjei."""

from datetime import date, timedelta

import pytest

from dombori.bartal_forecast import (
    ForecastError,
    _doy_distance,
    _k_day_changes,
    _percentile,
    compute_forecast,
)


def _flat_series(start: date, days: int, value: float = 100.0):
    return tuple((start + timedelta(days=i), value) for i in range(days))


def _seasonal_series(start: date, days: int):
    """Nyáron lassan csökkenő, télen emelkedő szintetikus sor."""
    rows = []
    value = 100.0
    for i in range(days):
        day = start + timedelta(days=i)
        value += -0.2 if 150 <= day.timetuple().tm_yday <= 250 else 0.1
        rows.append((day, round(value, 2)))
    return tuple(rows)


def test_doy_distance_wraps_around_new_year():
    assert _doy_distance(1, 366) == 1
    assert _doy_distance(10, 350) == 26
    assert _doy_distance(100, 100) == 0


def test_percentile_interpolates():
    values = (0.0, 10.0)
    assert _percentile(values, 0.5) == 5.0
    assert _percentile(values, 0.0) == 0.0
    assert _percentile(values, 1.0) == 10.0


def test_k_day_changes_respects_window():
    series = _flat_series(date(2020, 1, 1), 400)
    changes = _k_day_changes(series, anchor_doy=15, k=3, window=5)
    assert changes and all(c == 0.0 for c in changes)


def test_flat_series_forecast_is_persistence():
    series = _flat_series(date(2010, 1, 1), 5000)
    last_day, last_value = series[-1]
    points = compute_forecast(series, last_day, last_value)
    assert len(points) == 6
    assert all(p.value_cm == last_value for p in points)
    assert all(p.error_band_cm == 0.5 for p in points)  # minimum sáv
    assert points[0].target_ts.date() == last_day + timedelta(days=1)
    assert points[-1].target_ts.date() == last_day + timedelta(days=6)


def test_seasonal_series_forecast_has_drift_direction():
    series = _seasonal_series(date(2005, 1, 1), 7000)
    last_day, last_value = series[-1]
    points = compute_forecast(series, last_day, last_value)
    doy = last_day.timetuple().tm_yday
    expected_sign = -1 if 150 <= doy <= 250 else 1
    drift = points[-1].value_cm - last_value
    assert drift * expected_sign > 0


def test_short_series_raises():
    series = _flat_series(date(2024, 1, 1), 100)
    with pytest.raises(ForecastError):
        compute_forecast(series, series[-1][0], series[-1][1])


def test_forecast_values_are_immutable_inputs():
    series = _flat_series(date(2010, 1, 1), 3000)
    snapshot = tuple(series)
    compute_forecast(series, series[-1][0], series[-1][1])
    assert series == snapshot


def test_expand_to_6h_grid():
    from datetime import datetime
    from dombori.bartal_forecast import StatPoint, expand_to_6h_grid, TZ
    from datetime import time as dtime

    last_day = date(2026, 7, 29)
    daily = tuple(
        StatPoint(
            target_ts=datetime.combine(last_day + timedelta(days=k), dtime(12, 0), TZ),
            value_cm=100.0 + k,          # naponta +1 cm
            error_band_cm=float(k),      # naponta +1 cm sáv
        )
        for k in range(1, 7)
    )
    grid = expand_to_6h_grid(daily, last_day, 100.0)
    assert len(grid) == 24
    # első köztes pont: +6h, negyed napi drift
    assert grid[0].value_cm == 100.2 or grid[0].value_cm == 100.3
    # napi rácspontok pontosan a napi értékek
    assert grid[3].value_cm == 101.0 and grid[7].value_cm == 102.0
    assert grid[-1].value_cm == 106.0 and grid[-1].error_band_cm == 6.0
    # sáv monoton nem csökken és minimum 0.5
    bands = [p.error_band_cm for p in grid]
    assert bands == sorted(bands) and bands[0] >= 0.5
    # 6 órás lépésköz
    deltas = {(grid[i + 1].target_ts - grid[i].target_ts).total_seconds() for i in range(23)}
    assert deltas == {21600.0}


def test_trend_blending_pulls_forecast_down():
    from dombori.bartal_forecast import recent_trend_per_day

    series = _flat_series(date(2010, 1, 1), 5990) + tuple(
        (date(2010, 1, 1) + timedelta(days=5990 + i), 100.0 - 0.5 * (i + 1))
        for i in range(10)
    )
    last_day, last_value = series[-1]
    trend = recent_trend_per_day(series, last_day, last_value)
    assert trend == pytest.approx(-0.5, abs=0.01)
    points = compute_forecast(series, last_day, last_value, trend_per_day=trend)
    # 50% momentum: 6. napra kb. -1.5 cm (0.5 * -0.5 * 6), a szezonális ~0
    drift = points[-1].value_cm - last_value
    assert -2.5 < drift < -0.5


def test_zero_trend_keeps_persistence():
    series = _flat_series(date(2010, 1, 1), 5000)
    last_day, last_value = series[-1]
    from dombori.bartal_forecast import recent_trend_per_day
    assert recent_trend_per_day(series, last_day, last_value) == 0.0
