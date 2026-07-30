"""Gyűjtött állomások registry-je.

Egyetlen helyen tartja, mely állomásokat gyűjtjük a vizugy API-ból (Tsz)
és melyekhez tartozik Hydroinfo előrejelzési kód. Új állomás felvétele:
sor ide, majd `init-db` (seed) és `backfill --station <tsz>`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Station:
    tsz: int
    name: str
    hydroinfo_code: str | None  # None = nincs Hydroinfo előrejelzés


STATIONS: tuple[Station, ...] = (
    Station(5, "Duna–Komárom", "442522H"),
    Station(1026, "Duna–Budapest", "442027H"),
    Station(549, "Duna–Paks", "442030H"),
    Station(550, "Duna–Dombori", "442540H"),
    Station(831, "Duna–Mohács", "442032H"),
    Station(142062, "Fadd/Bartal zsilip", None),
)

ALL_TSZ: tuple[int, ...] = tuple(s.tsz for s in STATIONS)
HYDROINFO_CODES: tuple[str, ...] = tuple(
    s.hydroinfo_code for s in STATIONS if s.hydroinfo_code
)
