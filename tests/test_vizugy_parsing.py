"""Unit tests for observations.parse_timeseries and vizugy_client.token_expiry.

Pure parsing/decoding logic only -- no network, no database.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from dombori.observations import Observation, ParseError, parse_timeseries
from dombori.vizugy_client import token_expiry


# --------------------------------------------------------------------------
# parse_timeseries: happy path (real sample fixture)
# --------------------------------------------------------------------------


def test_parse_timeseries_happy_path_counts_and_stations(tsshortlist_payload):
    observations = parse_timeseries(tsshortlist_payload)

    assert len(observations) == 16
    stations = {o.station_tsz for o in observations}
    assert stations == {550, 142062}
    assert sum(1 for o in observations if o.station_tsz == 550) == 8
    assert sum(1 for o in observations if o.station_tsz == 142062) == 8


def test_parse_timeseries_happy_path_first_and_last_values(tsshortlist_payload):
    observations = parse_timeseries(tsshortlist_payload)

    first = observations[0]
    assert first == Observation(
        station_tsz=550,
        ts_utc=datetime(2005, 1, 1, 6, 0, 0, tzinfo=timezone.utc),
        value_cm=90.0,
    )

    last = observations[-1]
    assert last == Observation(
        station_tsz=142062,
        ts_utc=datetime(2005, 1, 8, 6, 0, 0, tzinfo=timezone.utc),
        value_cm=127.0,
    )


def test_parse_timeseries_datetimes_are_aware_utc(tsshortlist_payload):
    observations = parse_timeseries(tsshortlist_payload)

    for obs in observations:
        assert obs.ts_utc.tzinfo is not None
        assert obs.ts_utc.utcoffset() == timedelta(0)


def test_parse_timeseries_multi_station_preserves_order(tsshortlist_payload):
    observations = parse_timeseries(tsshortlist_payload)

    # Station 550's items come first in the payload, then 142062's.
    station_order = []
    for obs in observations:
        if not station_order or station_order[-1] != obs.station_tsz:
            station_order.append(obs.station_tsz)
    assert station_order == [550, 142062]


# --------------------------------------------------------------------------
# parse_timeseries: null/missing/non-numeric Adat is skipped, not an error
# --------------------------------------------------------------------------


def test_parse_timeseries_skips_null_adat():
    payload = [
        {
            "ItemId": 550,
            "TsItemList": [
                {"UTCTime": "2026-07-25T22:00:00Z", "Adat": None},
                {"UTCTime": "2026-07-25T23:00:00Z", "Adat": 12.3},
            ],
        }
    ]
    observations = parse_timeseries(payload)
    assert len(observations) == 1
    assert observations[0].value_cm == 12.3


def test_parse_timeseries_skips_missing_adat_key():
    payload = [
        {
            "ItemId": 550,
            "TsItemList": [
                {"UTCTime": "2026-07-25T22:00:00Z"},  # no "Adat" key at all
            ],
        }
    ]
    observations = parse_timeseries(payload)
    assert observations == ()


def test_parse_timeseries_skips_non_numeric_adat():
    payload = [
        {
            "ItemId": 550,
            "TsItemList": [
                {"UTCTime": "2026-07-25T22:00:00Z", "Adat": "N/A"},
                {"UTCTime": "2026-07-25T23:00:00Z", "Adat": 5.0},
            ],
        }
    ]
    observations = parse_timeseries(payload)
    assert len(observations) == 1
    assert observations[0].value_cm == 5.0


# --------------------------------------------------------------------------
# parse_timeseries: malformed payload -> ParseError
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"ItemId": 550}, id="dict-not-list"),
        pytest.param("just a string", id="string-not-list"),
        pytest.param(None, id="none-not-list"),
    ],
)
def test_parse_timeseries_rejects_non_list_payload(payload):
    with pytest.raises(ParseError):
        parse_timeseries(payload)


def test_parse_timeseries_rejects_non_dict_item():
    with pytest.raises(ParseError):
        parse_timeseries(["not-a-dict"])


def test_parse_timeseries_rejects_missing_item_id():
    with pytest.raises(ParseError):
        parse_timeseries([{"TsItemList": []}])


def test_parse_timeseries_rejects_missing_ts_item_list():
    with pytest.raises(ParseError):
        parse_timeseries([{"ItemId": 550}])


def test_parse_timeseries_rejects_non_list_ts_item_list():
    with pytest.raises(ParseError):
        parse_timeseries([{"ItemId": 550, "TsItemList": "oops"}])


def test_parse_timeseries_rejects_ts_item_missing_utctime():
    with pytest.raises(ParseError):
        parse_timeseries([{"ItemId": 550, "TsItemList": [{"Adat": 1.0}]}])


def test_parse_timeseries_rejects_non_dict_ts_item():
    with pytest.raises(ParseError):
        parse_timeseries([{"ItemId": 550, "TsItemList": ["oops"]}])


def test_parse_timeseries_rejects_invalid_item_id():
    with pytest.raises(ParseError):
        parse_timeseries([{"ItemId": "not-a-number", "TsItemList": []}])


def test_parse_timeseries_rejects_invalid_utctime():
    with pytest.raises(ParseError):
        parse_timeseries(
            [{"ItemId": 550, "TsItemList": [{"UTCTime": "not-a-date", "Adat": 1.0}]}]
        )


# --------------------------------------------------------------------------
# token_expiry: garbage token -> conservative ~now+5min fallback
# --------------------------------------------------------------------------


def _assert_is_fallback_expiry(expiry: datetime) -> None:
    now = datetime.now(timezone.utc)
    expected = now + timedelta(minutes=5)
    # Generous tolerance for test execution time, but tight enough to
    # distinguish from a real decoded 'exp' claim.
    assert abs((expiry - expected).total_seconds()) < 10


@pytest.mark.parametrize(
    "garbage_token",
    [
        pytest.param("not-a-jwt-at-all", id="no-dots"),
        pytest.param("only.two-parts", id="two-parts"),
        pytest.param("a.b.c.d", id="four-parts"),
        pytest.param("a." + "!!!not-base64!!!" + ".c", id="invalid-base64"),
        pytest.param(
            "a." + base64.urlsafe_b64encode(b"not json").decode().rstrip("=") + ".c",
            id="valid-base64-invalid-json",
        ),
        pytest.param(
            "a."
            + base64.urlsafe_b64encode(json.dumps({"sub": "x"}).encode()).decode().rstrip("=")
            + ".c",
            id="valid-json-missing-exp",
        ),
        pytest.param("", id="empty-string"),
    ],
)
def test_token_expiry_garbage_falls_back_conservatively(garbage_token):
    expiry = token_expiry(garbage_token)
    _assert_is_fallback_expiry(expiry)


def test_token_expiry_decodes_valid_exp_claim():
    exp_ts = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps({"exp": exp_ts}).encode()
    ).decode().rstrip("=")
    token = f"header.{payload_b64}.signature"

    expiry = token_expiry(token)
    assert expiry == datetime(2030, 1, 1, tzinfo=timezone.utc)
