"""Unit tests for dombori.hydroinfo's pure parsing functions.

No network access -- everything runs against the on-disk fixtures or small
hand-built HTML snippets exercising a single edge case.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dombori.hydroinfo import (
    ParseError,
    parse_imagemap,
    parse_issue_ts,
    parse_table,
    sha256_hex,
)

_BUDAPEST_TZ = ZoneInfo("Europe/Budapest")


# --------------------------------------------------------------------------
# parse_issue_ts
# --------------------------------------------------------------------------


def test_parse_issue_ts_from_table_fixture(table_html_text):
    issue_ts = parse_issue_ts(table_html_text)
    assert issue_ts == datetime(2026, 7, 27, 10, 33, tzinfo=_BUDAPEST_TZ)
    assert issue_ts.tzinfo is not None


def test_parse_issue_ts_missing_header_raises():
    with pytest.raises(ParseError):
        parse_issue_ts("<html><body>nothing here</body></html>")


def test_parse_issue_ts_unrecognized_month_raises():
    # A single (correctly double-escaped) unknown month-ish token.
    text = "Kiadva: 2026. xxxxxxx 27. 10:33"
    with pytest.raises(ParseError):
        parse_issue_ts(text)


# --------------------------------------------------------------------------
# parse_table
# --------------------------------------------------------------------------


def test_parse_table_point_counts(table_html_text):
    issue_ts = datetime(2026, 7, 27, 10, 33, tzinfo=_BUDAPEST_TZ)
    points = parse_table(table_html_text, issue_ts)

    observed = [p for p in points if p.point_type == "observed"]
    forecast = [p for p in points if p.point_type == "forecast"]
    assert len(points) == 25
    assert len(observed) == 1
    assert len(forecast) == 24


def test_parse_table_observed_point(table_html_text):
    issue_ts = datetime(2026, 7, 27, 10, 33, tzinfo=_BUDAPEST_TZ)
    points = parse_table(table_html_text, issue_ts)
    observed = [p for p in points if p.point_type == "observed"]

    point = observed[0]
    assert point.target_ts == datetime(2026, 7, 27, 7, 0, tzinfo=_BUDAPEST_TZ)
    assert point.value_cm == -87.0
    assert point.error_band_cm is None


def test_parse_table_first_and_last_forecast_points(table_html_text):
    issue_ts = datetime(2026, 7, 27, 10, 33, tzinfo=_BUDAPEST_TZ)
    points = parse_table(table_html_text, issue_ts)
    forecast = [p for p in points if p.point_type == "forecast"]

    first = forecast[0]
    assert first.target_ts == datetime(2026, 7, 27, 13, 0, tzinfo=_BUDAPEST_TZ)
    assert first.value_cm == -89.0
    assert first.error_band_cm == 1.0

    last = forecast[-1]
    assert last.target_ts == datetime(2026, 8, 2, 7, 0, tzinfo=_BUDAPEST_TZ)
    assert last.value_cm == -98.0
    assert last.error_band_cm == 27.0


def test_parse_table_no_table_raises():
    with pytest.raises(ParseError):
        parse_table("<html><body>no table here</body></html>", datetime.now(_BUDAPEST_TZ))


def test_parse_table_empty_table_raises():
    html = "<html><body><table></table></body></html>"
    with pytest.raises(ParseError):
        parse_table(html, datetime.now(_BUDAPEST_TZ))


# --------------------------------------------------------------------------
# parse_imagemap
# --------------------------------------------------------------------------


def test_parse_imagemap_point_counts(imagemap_html_text):
    points = parse_imagemap(imagemap_html_text)
    observed = [p for p in points if p.point_type == "observed"]
    forecast = [p for p in points if p.point_type == "forecast"]

    assert len(points) == 80
    assert len(observed) == 56
    assert len(forecast) == 24


def test_parse_imagemap_observed_day_part_labels_and_hours(imagemap_html_text):
    points = parse_imagemap(imagemap_html_text)
    observed_hours = {p.target_ts.hour for p in points if p.point_type == "observed"}
    # éjjel=01, reggel=07, délben=13, este=19
    assert observed_hours == {1, 7, 13, 19}


def test_parse_imagemap_observed_points_have_no_error_band(imagemap_html_text):
    points = parse_imagemap(imagemap_html_text)
    for p in points:
        if p.point_type == "observed":
            assert p.error_band_cm is None


def test_parse_imagemap_stub_areas_skipped(imagemap_html_text):
    # 82 <area> tags in the fixture, 2 of which are stub/empty titles;
    # parsing must silently skip those rather than raising or counting them.
    points = parse_imagemap(imagemap_html_text)
    assert len(points) == 80


def test_parse_imagemap_last_observed_and_forecast_points(imagemap_html_text):
    points = parse_imagemap(imagemap_html_text)
    observed = [p for p in points if p.point_type == "observed"]
    forecast = [p for p in points if p.point_type == "forecast"]

    assert observed[-1].target_ts == datetime(2026, 7, 27, 7, 0, tzinfo=_BUDAPEST_TZ)
    assert observed[-1].value_cm == -87.0

    assert forecast[0].target_ts == datetime(2026, 7, 27, 13, 0, tzinfo=_BUDAPEST_TZ)
    assert forecast[-1].target_ts == datetime(2026, 8, 2, 7, 0, tzinfo=_BUDAPEST_TZ)


def test_parse_imagemap_no_leading_zero_hour():
    html = (
        '<html><body><map name="m">'
        '<area shape="rect" coords="0,0,1,1" href="#" '
        'title="2026.07.28. 7:00, -93 cm">'
        "</map></body></html>"
    )
    points = parse_imagemap(html)
    assert len(points) == 1
    point = points[0]
    assert point.point_type == "forecast"
    assert point.target_ts == datetime(2026, 7, 28, 7, 0, tzinfo=_BUDAPEST_TZ)
    assert point.value_cm == -93.0
    assert point.error_band_cm is None


def test_parse_imagemap_all_stub_areas_raises():
    html = (
        '<html><body><map name="m">'
        '<area shape="rect" coords="0,0,1,1" href="#" title=",  cm">'
        '<area shape="rect" coords="0,0,1,1" href="#" title="">'
        "</map></body></html>"
    )
    with pytest.raises(ParseError):
        parse_imagemap(html)


def test_parse_imagemap_no_areas_raises():
    with pytest.raises(ParseError):
        parse_imagemap("<html><body>no map here</body></html>")


# --------------------------------------------------------------------------
# sha256_hex
# --------------------------------------------------------------------------


def test_sha256_hex_matches_hashlib_directly():
    data = b"some raw bytes from a fetch"
    assert sha256_hex(data) == hashlib.sha256(data).hexdigest()


def test_sha256_hex_is_stable_across_calls():
    data = b"stable input"
    assert sha256_hex(data) == sha256_hex(data)


def test_sha256_hex_differs_for_different_input():
    assert sha256_hex(b"a") != sha256_hex(b"b")
