#!/usr/bin/env python3
"""Az „Országos vízállás — Duna" dashboard generátora.

4 állomás (Komárom, Budapest, Paks, Mohács), állomásonként stat + saját
mért+előrejelzés panel (±sáv, LKV/LNV terület-küszöbök), a predikció az
utolsó méréshez horgonyozva. Szándékosan NINCSENEK holtág-annotációk:
azok Dombori-specifikusak.

Futtatás:  python3 grafana/build_orszagos.py > grafana/orszagos-vizallas.json
"""

from __future__ import annotations

import json

DS = {"type": "grafana-postgresql-datasource", "uid": "${DS_DOMBORI}"}

# (név, tsz, hydroinfo kód, LKV, LNV, stat-sárga küszöb, softMin, softMax, cím-kiegészítés)
STATIONS = (
    ("Komárom", 5, "442522H", -12, 845, 650, -60, 900, "belépés, 1768 fkm"),
    ("Budapest", 1026, "442027H", 33, 891, 700, -20, 950, "1647 fkm"),
    ("Paks", 549, "442030H", -97, 891, 700, -140, 950, "atomerőmű, 1531 fkm"),
    ("Mohács", 831, "442032H", 26, 984, 750, -20, 1040, "kilépés, 1447 fkm"),
)

MEASURED_SQL = (
    "SELECT ts_utc AS \"time\", value_cm AS \"mért\" FROM observations"
    " WHERE station_tsz = {tsz} AND $__timeFilter(ts_utc)"
    " UNION ALL SELECT (day_local::timestamp + interval '12 hours')"
    " AT TIME ZONE 'Europe/Budapest' AS \"time\", mean_cm FROM daily_aggregates"
    " WHERE station_tsz = {tsz} AND $__timeFilter(day_local)"
    " AND day_local < (now() AT TIME ZONE 'Europe/Budapest')::date - 27 ORDER BY 1"
)

FORECAST_SQL = (
    "WITH last_obs AS (SELECT ts_utc, value_cm FROM observations"
    " WHERE station_tsz = {tsz} ORDER BY ts_utc DESC LIMIT 1)"
    " SELECT ts_utc AS \"time\", value_cm AS \"előrejelzés\" FROM last_obs"
    " UNION ALL SELECT target_ts, value_cm FROM forecast_points"
    " WHERE point_type = 'forecast' AND target_ts > (SELECT ts_utc FROM last_obs)"
    " AND run_id = (SELECT id FROM forecast_runs WHERE station_code = '{code}'"
    " ORDER BY issue_ts DESC LIMIT 1) ORDER BY 1"
)

BAND_SQL = (
    "WITH last_obs AS (SELECT ts_utc, value_cm FROM observations"
    " WHERE station_tsz = {tsz} ORDER BY ts_utc DESC LIMIT 1)"
    " SELECT ts_utc AS \"time\", value_cm AS \"felső\", value_cm AS \"alsó\" FROM last_obs"
    " UNION ALL SELECT target_ts, value_cm + coalesce(error_band_cm, 0),"
    " value_cm - coalesce(error_band_cm, 0) FROM forecast_points"
    " WHERE point_type = 'forecast' AND target_ts > (SELECT ts_utc FROM last_obs)"
    " AND run_id = (SELECT id FROM forecast_runs WHERE station_code = '{code}'"
    " ORDER BY issue_ts DESC LIMIT 1) ORDER BY 1"
)


def _fixed(color: str) -> dict:
    return {"id": "color", "value": {"mode": "fixed", "fixedColor": color}}


def stat_panel(pid, x, name, extra, tsz, lkv, lnv, yellow) -> dict:
    return {
        "id": pid, "type": "stat", "title": f"{name} ({extra})",
        "description": f"Tsz {tsz} · LKV {lkv} cm · LNV {lnv} cm",
        "gridPos": {"x": x, "y": 0, "w": 6, "h": 5}, "datasource": DS,
        "targets": [{"refId": "A", "format": "table", "datasource": DS,
                     "rawSql": f"SELECT ts_utc AS \"time\", value_cm FROM observations"
                               f" WHERE station_tsz = {tsz} ORDER BY ts_utc DESC LIMIT 1"}],
        "options": {"colorMode": "background", "graphMode": "none", "textMode": "value",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "/^value_cm$/",
                                      "values": False}},
        "fieldConfig": {"defaults": {"unit": "lengthcm", "decimals": 0,
                                     "thresholds": {"mode": "absolute", "steps": [
                                         {"color": "blue", "value": None},
                                         {"color": "green", "value": lkv},
                                         {"color": "#EAB839", "value": yellow},
                                         {"color": "red", "value": lnv}]}},
                        "overrides": []}}


def station_panel(pid, x, y, name, tsz, code, lkv, lnv, soft_min, soft_max) -> dict:
    return {
        "id": pid, "type": "timeseries",
        "title": f"{name}: mért + előrejelzés (±sáv)",
        "description": f"Zöld terület LKV ({lkv} cm) fölött, piros LNV ({lnv} cm) fölött.",
        "gridPos": {"x": x, "y": y, "w": 12, "h": 10}, "datasource": DS,
        "targets": [
            {"refId": "A", "format": "time_series", "datasource": DS,
             "rawSql": MEASURED_SQL.format(tsz=tsz)},
            {"refId": "B", "format": "time_series", "datasource": DS,
             "rawSql": FORECAST_SQL.format(tsz=tsz, code=code)},
            {"refId": "C", "format": "time_series", "datasource": DS,
             "rawSql": BAND_SQL.format(tsz=tsz, code=code)},
        ],
        "options": {"legend": {"calcs": [], "displayMode": "list", "enableFacetedFilter": False,
                               "overflow": "ellipsis", "placement": "bottom", "showLegend": True},
                    "tooltip": {"hideZeros": False, "mode": "multi", "sort": "none"}},
        "fieldConfig": {
            "defaults": {"unit": "lengthcm",
                         "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0,
                                    "spanNulls": True, "showPoints": "auto", "pointSize": 5,
                                    "thresholdsStyle": {"mode": "area"},
                                    "axisSoftMin": soft_min, "axisSoftMax": soft_max},
                         "thresholds": {"mode": "absolute", "steps": [
                             {"color": "transparent", "value": None},
                             {"color": "green", "value": lkv},
                             {"color": "red", "value": lnv}]}},
            "overrides": [
                {"matcher": {"id": "byName", "options": "mért"}, "properties": [_fixed("blue")]},
                {"matcher": {"id": "byName", "options": "előrejelzés"},
                 "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}},
                                _fixed("orange")]},
                {"matcher": {"id": "byName", "options": "felső"},
                 "properties": [{"id": "custom.lineWidth", "value": 0},
                                {"id": "custom.fillBelowTo", "value": "alsó"},
                                {"id": "custom.fillOpacity", "value": 15}, _fixed("orange"),
                                {"id": "custom.hideFrom",
                                 "value": {"legend": True, "tooltip": True, "viz": False}}]},
                {"matcher": {"id": "byName", "options": "alsó"},
                 "properties": [{"id": "custom.lineWidth", "value": 0}, _fixed("orange"),
                                {"id": "custom.hideFrom",
                                 "value": {"legend": True, "tooltip": True, "viz": False}}]},
            ]}}


def build() -> dict:
    panels = []
    for i, (name, tsz, code, lkv, lnv, yellow, smin, smax, extra) in enumerate(STATIONS):
        panels.append(stat_panel(i + 1, i * 6, name, extra, tsz, lkv, lnv, yellow))
    for i, (name, tsz, code, lkv, lnv, yellow, smin, smax, extra) in enumerate(STATIONS):
        panels.append(station_panel(i + 6, (i % 2) * 12, 5 + (i // 2) * 10,
                                    name, tsz, code, lkv, lnv, smin, smax))
    return {
        "annotations": {"list": [{"builtIn": 1,
                                  "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                                  "enable": True, "hide": True,
                                  "iconColor": "rgba(0, 211, 255, 1)",
                                  "name": "Annotations & Alerts", "type": "dashboard"}]},
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 0, "id": None,
        "liveNow": False,
        "links": [{"asDropdown": true, "icon": "external link", "includeVars": false, "keepTime": false, "tags": ["vízállás"], "targetBlank": false, "title": "menü", "tooltip": "", "type": "dashboards", "url": ""}],
        "panels": panels, "preload": False, "refresh": "5m", "schemaVersion": 42,
        "tags": ["dombori", "vizallas", "duna", "orszagos", "vízállás"],
        "time": {"from": "now-28d", "to": "now+6d"}, "timepicker": {},
        "timezone": "Europe/Budapest", "title": "Országos vízállás — Duna",
        "uid": "orszagos-vizallas", "weekStart": "monday",
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
