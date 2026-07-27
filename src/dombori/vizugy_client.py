"""Client for the vizugy.hu / vmservice.vizugy.hu time-series API."""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

TOKEN_URL = "https://data.vizugy.hu/AuthApi/auth/token"
TS_URL = "https://vmservice.vizugy.hu/vraquery/TS/TsShortList"
STATIONS_URL = "https://vmservice.vizugy.hu/vraquery/Vra/InternetVmo/11/false"

# The token endpoint 403s on requests that don't look like they came from
# the data.vizugy.hu web app.
_TOKEN_HEADERS = {
    "Origin": "https://data.vizugy.hu",
    "Referer": "https://data.vizugy.hu/",
}

_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


class VizugyError(RuntimeError):
    """Raised when the vizugy.hu API returns an unexpected response."""


def fetch_token(session: requests.Session) -> str:
    """Fetch a bearer token from the vizugy.hu auth endpoint."""
    try:
        response = session.get(TOKEN_URL, headers=_TOKEN_HEADERS, timeout=30)
    except requests.RequestException as exc:
        raise VizugyError(f"Token request failed: {exc}") from exc

    if response.status_code != 200:
        raise VizugyError(
            f"Token request returned status {response.status_code}: {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise VizugyError(f"Token response was not valid JSON: {exc}") from exc

    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise VizugyError(f"Token response missing 'access_token': {payload!r}")
    return token


def token_expiry(token: str) -> datetime:
    """Return the UTC expiry of a JWT, decoded from its 'exp' claim.

    Any failure to decode the token conservatively yields "now + 5 minutes"
    so callers refresh sooner rather than risk using a dead token.
    """
    fallback = datetime.now(timezone.utc) + timedelta(minutes=5)
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return fallback
        payload_b64 = parts[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(padded)
        payload = json.loads(payload_bytes)
        exp = payload.get("exp")
        if exp is None:
            return fallback
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except (ValueError, TypeError, binascii.Error, json.JSONDecodeError):
        return fallback


def fetch_timeseries(
    session: requests.Session,
    token: str,
    tsz_list: list[int],
    start_local: datetime,
    end_local: datetime,
) -> list[dict]:
    """POST TsShortList for the given stations and local time window.

    ``start_local``/``end_local`` are naive local (Europe/Budapest) times as
    the API expects; the response's own timestamps come back in UTC.
    """
    body = {
        "torzsszamList": list(tsz_list),
        "adatFajtaKod": 68,
        "adatTipusKod": 100,
        "startTime": start_local.strftime(_DATETIME_FMT),
        "endTime": end_local.strftime(_DATETIME_FMT),
        "dataExtFilter": 0,
        "valueFilter": "Relativ",
        "amKodFilter": [0],
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = session.post(TS_URL, json=body, headers=headers, timeout=120)
    except requests.RequestException as exc:
        raise VizugyError(f"TsShortList request failed: {exc}") from exc

    if response.status_code != 200:
        raise VizugyError(
            f"TsShortList returned status {response.status_code}: {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise VizugyError(f"TsShortList response was not valid JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise VizugyError(f"TsShortList response was not a list: {type(payload)}")
    for item in payload:
        if not isinstance(item, dict) or "ItemId" not in item or "TsItemList" not in item:
            raise VizugyError(f"TsShortList item malformed: {item!r}")

    return payload


def fetch_stations(session: requests.Session, token: str) -> list[dict]:
    """Fetch the full station list (requires a Bearer token)."""
    try:
        response = session.get(
            STATIONS_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
    except requests.RequestException as exc:
        raise VizugyError(f"Stations request failed: {exc}") from exc

    if response.status_code != 200:
        raise VizugyError(
            f"Stations request returned status {response.status_code}: {response.text[:200]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise VizugyError(f"Stations response was not valid JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise VizugyError(f"Stations response was not a list: {type(payload)}")
    return payload
