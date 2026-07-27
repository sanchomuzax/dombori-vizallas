"""Fetching and parsing of hydroinfo.hu forecast pages for station 442540H.

hydroinfo.hu serves two representations of the same forecast:

* a small HTML `<table>` (``TABLE_URL``) -- the primary source, and
* an image with an HTML `<map>` of clickable areas (``IMAGEMAP_URL``) --
  used as a fallback when the table page can't be parsed.

Both pages declare (and are served as) ISO-8859-2, though the imagemap page
in practice contains UTF-8 bytes mis-decoded as ISO-8859-2 by the origin
server, producing mojibake in accented Hungarian text. ``parse_imagemap``
works around this; see ``_fix_mojibake``.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

TABLE_URL = "https://www.hydroinfo.hu/tables/442540H.html"
IMAGEMAP_URL = "https://www.hydroinfo.hu/Html/hidelo/elore.php?all=442540H"
STATION_CODE = "442540H"

_BUDAPEST_TZ = ZoneInfo("Europe/Budapest")

# Hydroinfo's day-part labels for "observed" readings, mapped to the local
# clock hour they represent.
_DAY_PART_HOURS = {"éjjel": 1, "reggel": 7, "délben": 13, "este": 19}

_HU_MONTHS = {
    "január": 1,
    "február": 2,
    "március": 3,
    "április": 4,
    "május": 5,
    "június": 6,
    "július": 7,
    "augusztus": 8,
    "szeptember": 9,
    "október": 10,
    "november": 11,
    "december": 12,
}
# Accent-stripped prefixes, used only if an exact (accented) name doesn't
# match -- covers further mangled encodings we haven't seen yet.
_HU_MONTH_PREFIXES: tuple[tuple[tuple[str, ...], int], ...] = (
    (("jan",), 1),
    (("feb",), 2),
    (("mar",), 3),
    (("apr",), 4),
    (("maj",), 5),
    (("jun",), 6),
    (("jul",), 7),
    (("aug",), 8),
    (("szep",), 9),
    (("okt",), 10),
    (("nov",), 11),
    (("dec",), 12),
)

_ISSUE_RE = re.compile(
    r"(\d{4})\.\s*([^\W\d_]+)\s+(\d{1,2})\.\s+(\d{1,2}):(\d{2})", re.UNICODE
)
_LABEL_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\.\s*(.+)$")
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_BAND_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_IMAGEMAP_TITLE_RE = re.compile(
    r"^(\d{4})\.(\d{2})\.(\d{2})\.\s*([^,]+),\s*(-?\d+(?:\.\d+)?)\s*cm$"
)


class HydroinfoError(RuntimeError):
    """Raised when fetching a hydroinfo.hu page fails outright."""


class ParseError(ValueError):
    """Raised when a hydroinfo.hu page can't be parsed as expected."""


@dataclass(frozen=True)
class FetchResult:
    status: int
    raw: bytes | None
    etag: str | None
    text: str | None


@dataclass(frozen=True)
class ForecastPoint:
    target_ts: datetime
    value_cm: float
    error_band_cm: float | None
    point_type: str  # 'observed' | 'forecast'


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fetch(url: str, session: requests.Session, headers: dict[str, str]) -> requests.Response:
    # Deliberately do not set a browser-spoofing User-Agent: hydroinfo.hu's
    # WAF blocks requests that look like a browser but aren't; the plain
    # `python-requests/x.y` default UA is what actually works.
    try:
        return session.get(url, headers=headers, timeout=60)
    except requests.RequestException as exc:
        raise HydroinfoError(f"GET {url} failed: {exc}") from exc


def fetch_table(session: requests.Session, etag: str | None = None) -> FetchResult:
    """Fetch the forecast table page, honoring a prior ETag if given."""
    headers = {"If-None-Match": etag} if etag else {}
    response = _fetch(TABLE_URL, session, headers)

    if response.status_code == 304:
        return FetchResult(status=304, raw=None, etag=etag, text=None)
    if response.status_code != 200:
        raise HydroinfoError(
            f"Table fetch returned status {response.status_code} for {TABLE_URL}"
        )

    raw = response.content
    text = raw.decode("iso-8859-2")
    return FetchResult(status=200, raw=raw, etag=response.headers.get("ETag"), text=text)


def fetch_imagemap(session: requests.Session) -> FetchResult:
    """Fetch the imagemap fallback page. No conditional-GET support."""
    response = _fetch(IMAGEMAP_URL, session, {})
    if response.status_code != 200:
        raise HydroinfoError(
            f"Imagemap fetch returned status {response.status_code} for {IMAGEMAP_URL}"
        )
    raw = response.content
    text = raw.decode("iso-8859-2")
    return FetchResult(status=200, raw=raw, etag=None, text=text)


def _month_number(name: str) -> int:
    lname = name.lower()
    if lname in _HU_MONTHS:
        return _HU_MONTHS[lname]
    for prefixes, number in _HU_MONTH_PREFIXES:
        if any(lname.startswith(prefix) for prefix in prefixes):
            return number
    raise ParseError(f"Unrecognized Hungarian month name: {name!r}")


def parse_issue_ts(text: str) -> datetime:
    """Parse the "Kiadva: <date>" header into an aware Europe/Budapest datetime.

    The month name in the raw page is HTML-entity-escaped *twice* (e.g.
    ``j&amp;uacutelius`` for "július"), so we run ``html.unescape`` twice
    before matching -- the first pass turns ``&amp;`` into ``&``, the
    second resolves the resulting (semicolon-less) named entity.
    """
    idx = text.find("Kiadva")
    if idx == -1:
        raise ParseError("'Kiadva' header not found in table page")

    window = text[idx : idx + 200]
    fixed = html.unescape(html.unescape(window))

    match = _ISSUE_RE.search(fixed)
    if not match:
        raise ParseError(f"Could not parse issue timestamp near 'Kiadva': {fixed!r}")

    year, month_name, day, hour, minute = match.groups()
    month = _month_number(month_name)
    naive = datetime(int(year), month, int(day), int(hour), int(minute))
    return naive.replace(tzinfo=_BUDAPEST_TZ)


def parse_table(text: str, issue_ts: datetime) -> tuple[ForecastPoint, ...]:
    """Parse the forecast `<table>` into observed + forecast points.

    ``issue_ts`` is accepted for interface symmetry with the imagemap path
    and is used only for a sanity-check log line -- every row in the table
    already carries its own full year/month/day.
    """
    soup = BeautifulSoup(text, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ParseError("No <table> found in hydroinfo table page")

    points: list[ForecastPoint] = []
    for tr in table.find_all("tr", recursive=False):
        tds = tr.find_all("td", recursive=False)
        if len(tds) != 2:
            continue  # title/station-name/Kiadva/footer rows: single td, colspan=2

        label = tds[0].get_text(strip=True)
        match = _LABEL_RE.match(label)
        if not match:
            continue

        year, month, day, rest = match.groups()
        rest = rest.strip()
        nested = tds[1].find("table")

        if nested is None:
            point = _parse_observed_row(year, month, day, rest, tds[1])
        else:
            point = _parse_forecast_row(year, month, day, rest, nested)
        points.append(point)

    if not points:
        raise ParseError("No forecast points found in table page")

    logger.debug(
        "Parsed %d table points for issue_ts=%s", len(points), issue_ts.isoformat()
    )
    return tuple(points)


def _parse_observed_row(year: str, month: str, day: str, rest: str, value_td) -> ForecastPoint:
    hour = _DAY_PART_HOURS.get(rest.lower())
    if hour is None:
        raise ParseError(f"Unrecognized day-part label: {rest!r}")
    target_ts = datetime(int(year), int(month), int(day), hour, 0, tzinfo=_BUDAPEST_TZ)

    value_text = value_td.get_text(strip=True)
    try:
        value_cm = float(value_text)
    except ValueError as exc:
        raise ParseError(f"Invalid observed value {value_text!r}: {exc}") from exc

    return ForecastPoint(target_ts, value_cm, None, "observed")


def _parse_forecast_row(year: str, month: str, day: str, rest: str, nested) -> ForecastPoint:
    clock = _CLOCK_RE.match(rest)
    if not clock:
        raise ParseError(f"Unrecognized forecast time label: {rest!r}")
    hour, minute = int(clock.group(1)), int(clock.group(2))
    target_ts = datetime(int(year), int(month), int(day), hour, minute, tzinfo=_BUDAPEST_TZ)

    inner_tds = nested.find_all("td")
    if len(inner_tds) < 2:
        raise ParseError(f"Forecast row missing value/band cells for {year}.{month}.{day}")

    value_text = inner_tds[0].get_text(strip=True)
    try:
        value_cm = float(value_text)
    except ValueError as exc:
        raise ParseError(f"Invalid forecast value {value_text!r}: {exc}") from exc

    band_text = inner_tds[1].get_text(strip=True)
    band_match = _BAND_RE.search(band_text)
    if not band_match:
        raise ParseError(f"Invalid error band {band_text!r}")
    error_band_cm = float(band_match.group())

    return ForecastPoint(target_ts, value_cm, error_band_cm, "forecast")


def _fix_mojibake(text: str) -> str:
    """Undo "UTF-8 bytes decoded as ISO-8859-2" mangling, if present.

    Safe no-op for genuinely ISO-8859-2 text: re-encoding ASCII round-trips
    losslessly, and re-encoding *real* single-byte Latin-2 accented chars as
    UTF-8 continuation bytes almost always fails to decode, so we fall back
    to the original text instead of corrupting it further.
    """
    try:
        fixed = text.encode("iso-8859-2").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return fixed


def parse_imagemap(text: str) -> tuple[ForecastPoint, ...]:
    """Parse the `<area title="...">` fallback page.

    Day-part-labeled areas (e.g. "délben") are observed points; clock-time
    areas (e.g. "13:00", possibly without a leading zero) are forecast
    points with no error band (the imagemap doesn't carry one). Empty stub
    areas (``title=",  cm"``) are skipped.
    """
    fixed_text = _fix_mojibake(text)
    soup = BeautifulSoup(fixed_text, "html.parser")
    areas = soup.find_all("area")
    if not areas:
        raise ParseError("No <area> elements found in imagemap page")

    points: list[ForecastPoint] = []
    for area in areas:
        title = (area.get("title") or "").strip()
        match = _IMAGEMAP_TITLE_RE.match(title)
        if not match:
            logger.debug("Skipping unrecognized/empty imagemap area title: %r", title)
            continue

        year, month, day, rest, value_text = match.groups()
        rest = rest.strip()
        value_cm = float(value_text)

        day_part_hour = _DAY_PART_HOURS.get(rest.lower())
        if day_part_hour is not None:
            target_ts = datetime(
                int(year), int(month), int(day), day_part_hour, 0, tzinfo=_BUDAPEST_TZ
            )
            points.append(ForecastPoint(target_ts, value_cm, None, "observed"))
            continue

        clock = _CLOCK_RE.match(rest)
        if not clock:
            raise ParseError(f"Unrecognized imagemap time label: {rest!r}")
        hour, minute = int(clock.group(1)), int(clock.group(2))
        target_ts = datetime(int(year), int(month), int(day), hour, minute, tzinfo=_BUDAPEST_TZ)
        points.append(ForecastPoint(target_ts, value_cm, None, "forecast"))

    if not points:
        raise ParseError("No usable points parsed from imagemap page")
    return tuple(points)
