#!/usr/bin/env python3
"""A szezonális dashboard JSON-jának előállítása.

A dashboard évenként egy-egy sorozatot rajzol egymásra, és minden évhez saját
szín-override tartozik (sötétszürke → kék gradiens). Ez ~130 override
panelenként, amit generálni sokkal olvashatóbb, mint kézzel verziózni.

Futtatás:
  python3 grafana/build_szezonalis.py > grafana/dombori-szezonalis.json
  python3 grafana/build_szezonalis.py orszagos > grafana/orszagos-szezonalis.json

Az országos változat évtizedes (nem évenkénti) gradienst használ: negyed
annyi override, szemre azonos hatás.
"""

from __future__ import annotations

import json

DS = "${DS_DOMBORI}"
GRAD_START = (58, 58, 62)      # #3a3a3e - legrégebbi év
GRAD_END = (61, 113, 217)      # #3d71d9 - 2024
GRAD_END_YEAR = 2024
# 2025 utáni évek előre kiosztott színe, hogy a jövő is a skálán maradjon
FUTURE_COLORS = [
    "#4074da", "#4276dc", "#4479dd", "#467cdf", "#487fe0", "#4a82e2",
    "#4c85e3", "#4e88e5", "#508be6", "#528ee8", "#5491e9",
]
FUTURE_START_YEAR = 2025

MEASURED_SQL = (
    "SELECT make_date(extract(year FROM now())::int, extract(month FROM day_local)::int,"
    " extract(day FROM day_local)::int)::timestamptz AS \"time\","
    " CASE WHEN extract(year FROM day_local) = extract(year FROM now())"
    " THEN 'idei (' || extract(year FROM day_local)::int::text || ')'"
    " WHEN extract(year FROM day_local) = extract(year FROM now()) - 1"
    " THEN 'tavalyi (' || extract(year FROM day_local)::int::text || ')'"
    " ELSE extract(year FROM day_local)::text END AS metric, mean_cm"
    " FROM daily_aggregates WHERE station_tsz = {tsz}"
    " AND NOT (extract(month FROM day_local) = 2 AND extract(day FROM day_local) = 29"
    " AND NOT ((extract(year FROM now())::int % 4 = 0"
    " AND extract(year FROM now())::int % 100 <> 0)"
    " OR extract(year FROM now())::int % 400 = 0)) ORDER BY 1"
)

FORECAST_SQL = (
    "SELECT make_date(extract(year FROM now())::int,"
    " extract(month FROM (target_ts AT TIME ZONE 'Europe/Budapest'))::int,"
    " extract(day FROM (target_ts AT TIME ZONE 'Europe/Budapest'))::int)::timestamptz AS \"time\","
    " round(avg(value_cm), 1) AS \"előrejelzés\" FROM forecast_points"
    " WHERE point_type = 'forecast' AND run_id = (SELECT id FROM forecast_runs"
    " WHERE {run_filter} ORDER BY issue_ts DESC LIMIT 1) GROUP BY 1 ORDER BY 1"
)


ANCHORED_FORECAST_SQL = (
    "SELECT make_date(extract(year FROM now())::int, extract(month FROM day_local)::int,"
    " extract(day FROM day_local)::int)::timestamptz AS \"time\", mean_cm AS \"előrejelzés\""
    " FROM daily_aggregates WHERE station_tsz = {tsz} AND day_local ="
    " (SELECT max(day_local) FROM daily_aggregates WHERE station_tsz = {tsz})"
    " UNION ALL SELECT make_date(extract(year FROM now())::int,"
    " extract(month FROM (target_ts AT TIME ZONE 'Europe/Budapest'))::int,"
    " extract(day FROM (target_ts AT TIME ZONE 'Europe/Budapest'))::int)::timestamptz,"
    " round(avg(value_cm), 1) FROM forecast_points WHERE point_type = 'forecast'"
    " AND (target_ts AT TIME ZONE 'Europe/Budapest')::date >"
    " (SELECT max(day_local) FROM daily_aggregates WHERE station_tsz = {tsz})"
    " AND run_id = (SELECT id FROM forecast_runs WHERE {run_filter}"
    " ORDER BY issue_ts DESC LIMIT 1) GROUP BY 1 ORDER BY 1"
)


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def year_color(year: int, first_year: int) -> str:
    """Lineáris gradiens az első évtől 2024-ig, utána fix jövő-paletta."""
    if year >= FUTURE_START_YEAR:
        idx = min(year - FUTURE_START_YEAR, len(FUTURE_COLORS) - 1)
        return FUTURE_COLORS[idx]
    t = (year - first_year) / (GRAD_END_YEAR - first_year)
    return "#{:02x}{:02x}{:02x}".format(
        *(_lerp(GRAD_START[i], GRAD_END[i], t) for i in range(3))
    )


def _fixed(color: str) -> dict:
    return {"id": "color", "value": {"fixedColor": color, "mode": "fixed"}}


def _visible() -> dict:
    return {"id": "custom.hideFrom", "value": {"legend": False, "tooltip": False, "viz": False}}


def overrides(first_year: int, with_forecast: bool) -> list[dict]:
    out = [
        {"matcher": {"id": "byRegexp", "options": f"^{y}$"},
         "properties": [_fixed(year_color(y, first_year))]}
        for y in range(first_year, FUTURE_START_YEAR + len(FUTURE_COLORS))
    ]
    out.append({"matcher": {"id": "byRegexp", "options": "^tavalyi.*"},
                "properties": [_fixed("orange"), {"id": "custom.lineWidth", "value": 2}, _visible()]})
    out.append({"matcher": {"id": "byRegexp", "options": "^idei.*"},
                "properties": [_fixed("red"), {"id": "custom.lineWidth", "value": 3}, _visible()]})
    if with_forecast:
        out.append({"matcher": {"id": "byName", "options": "előrejelzés"},
                    "properties": [_fixed("dark-red"), {"id": "custom.lineWidth", "value": 3},
                                   {"id": "custom.lineStyle", "value": {"dash": [3, 3], "fill": "dash"}},
                                   _visible()]})
    return out


def decade_overrides(first_year: int, with_forecast: bool) -> list[dict]:
    """Évtizedenkénti gradiens-override-ok (könnyű változat, ~15 szabály)."""
    out = []
    for decade in range(first_year // 10, 204):
        mid = min(decade * 10 + 5, FUTURE_START_YEAR + len(FUTURE_COLORS) - 1)
        out.append({"matcher": {"id": "byRegexp", "options": f"^{decade}\\d$"},
                    "properties": [_fixed(year_color(max(mid, first_year), first_year))]})
    out.append({"matcher": {"id": "byRegexp", "options": "^tavalyi.*"},
                "properties": [_fixed("orange"), {"id": "custom.lineWidth", "value": 2}, _visible()]})
    out.append({"matcher": {"id": "byRegexp", "options": "^idei.*"},
                "properties": [_fixed("red"), {"id": "custom.lineWidth", "value": 3}, _visible()]})
    if with_forecast:
        out.append({"matcher": {"id": "byName", "options": "előrejelzés"},
                    "properties": [_fixed("dark-red"), {"id": "custom.lineWidth", "value": 3},
                                   {"id": "custom.lineStyle", "value": {"dash": [3, 3], "fill": "dash"}},
                                   _visible()]})
    return out


def custom_defaults(threshold_mode: str) -> dict:
    return {
        "axisBorderShow": False, "axisCenteredZero": False, "axisColorMode": "text",
        "axisLabel": "napi átlag vízállás", "axisPlacement": "auto", "barAlignment": 0,
        "barWidthFactor": 0.6, "drawStyle": "line", "fillOpacity": 0, "gradientMode": "none",
        "hideFrom": {"legend": True, "tooltip": False, "viz": False}, "insertNulls": False,
        "lineInterpolation": "linear", "lineWidth": 1, "pointSize": 5,
        "scaleDistribution": {"type": "linear"}, "showPoints": "never", "showValues": False,
        "spanNulls": True, "stacking": {"group": "A", "mode": "none"},
        "thresholdsStyle": {"mode": threshold_mode},
    }


def panel(*, pid, tsz, title, description, first_year_var, y, thresholds,
          threshold_mode, run_filter, forecast, decades=False, anchored=False) -> dict:
    targets = [{"datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
                "format": "time_series", "refId": "A",
                "rawSql": MEASURED_SQL.format(tsz=tsz)}]
    if forecast:
        targets.append({"datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
                        "format": "time_series", "refId": "B",
                        "rawSql": (ANCHORED_FORECAST_SQL if anchored else FORECAST_SQL).format(run_filter=run_filter, tsz=tsz)})
    return {
        "datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
        "description": description,
        "fieldConfig": {
            "defaults": {"color": {"fixedColor": "#5a5a5a", "mode": "fixed"},
                         "custom": custom_defaults(threshold_mode),
                         "thresholds": {"mode": "absolute", "steps": thresholds},
                         "unit": "lengthcm"},
            "overrides": (decade_overrides if decades else overrides)(first_year_var, forecast),
        },
        "gridPos": {"h": 13, "w": 24, "x": 0, "y": y},
        "id": pid,
        "options": {"annotations": {"clustering": -1, "multiLane": False},
                    "legend": {"calcs": [], "displayMode": "list", "enableFacetedFilter": False,
                               "overflow": "ellipsis", "placement": "bottom", "showLegend": True},
                    "tooltip": {"hideZeros": False, "mode": "single", "sort": "none"}},
        "pluginVersion": "13.1.1",
        "targets": targets,
        "title": title,
        "type": "timeseries",
    }


def variable(name: str, label: str, query: str) -> dict:
    return {"allowCustomValue": True, "current": {"text": "", "value": ""},
            "datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
            "definition": query, "hide": 2, "includeAll": False, "label": label,
            "multi": False, "name": name, "options": [], "query": query, "refresh": 1,
            "regex": "", "regexApplyTo": "value", "skipUrlSync": False, "sort": 0,
            "type": "query"}


def build() -> dict:
    return {
        "annotations": {"list": [
            {"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"},
             "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)",
             "name": "Annotations & Alerts", "type": "dashboard"},
            {"datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
             "enable": True, "hide": False, "iconColor": "green",
             "name": "Pozitív vízügyi események",
             "target": {"editorMode": "code", "format": "table", "rawQuery": True, "refId": "Anno",
                        "rawSql": "SELECT start_date::timestamptz AS time, end_date::timestamptz AS timeend,"
                                  " title || ' - ' || coalesce(location, '') AS text, event_type AS tags"
                                  " FROM holtag_events WHERE impact = 'pozitiv' ORDER BY start_date"}},
            {"datasource": {"type": "grafana-postgresql-datasource", "uid": DS},
             "enable": True, "hide": False, "iconColor": "red",
             "name": "Negatív vízügyi események",
             "target": {"editorMode": "code", "format": "table", "rawQuery": True, "refId": "Anno",
                        "rawSql": "SELECT start_date::timestamptz AS time, end_date::timestamptz AS timeend,"
                                  " title || ' - ' || coalesce(location, '') AS text, event_type AS tags"
                                  " FROM holtag_events WHERE impact = 'negativ' ORDER BY start_date"}},
        ]},
        "editable": True, "fiscalYearStartMonth": 0, "graphTooltip": 0, "id": None, "liveNow": False,
        "panels": [
            panel(pid=1, tsz=550, y=0, first_year_var=1901,
                  title="Napi átlag vízállás évenként egymáson - Duna-Dombori (${elso_ev}-${aktualis_ev})",
                  description="Minden év az aktuális év naptárára vetítve - évfüggetlen, magától"
                              " frissül. Színskála: sötétszürke (1901) → kék; narancs = tavalyi,"
                              " vastag piros = idei év; halványpiros szaggatott = Hydroinfo"
                              " előrejelzés. Zöld vonal: 400 cm - Dombori szivornya minimum.",
                  thresholds=[{"color": "transparent", "value": 0}, {"color": "green", "value": 400}],
                  threshold_mode="line", run_filter="station_code = '442540H'", forecast=True),
            panel(pid=2, tsz=142062, y=13, first_year_var=1973,
                  title="Napi átlag vízállás évenként egymáson - Fadd/Bartal zsilip"
                        " (${elso_ev_bartal}-${aktualis_ev})",
                  description="Minden év az aktuális év naptárára vetítve - évfüggetlen."
                              " Színskála: sötétszürke (1973) → kék; narancs = tavalyi, vastag"
                              " piros = idei év; halványpiros szaggatott = statisztikai előrejelzés."
                              " Küszöbvonalak: KF I 170 (sárga), KF II 190 (narancs),"
                              " KF III 200 cm (piros).",
                  thresholds=[{"color": "transparent", "value": 0}, {"color": "yellow", "value": 170},
                              {"color": "orange", "value": 190}, {"color": "red", "value": 200}],
                  threshold_mode="area",
                  run_filter="station_code = '142062' AND source = 'statisztikai'", forecast=True),
        ],
        "preload": False, "refresh": "", "schemaVersion": 42,
        "tags": ["dombori", "vizallas", "duna", "vízállás"],
        "links": [{"asDropdown": True, "icon": "external link", "includeVars": False, "keepTime": False, "tags": ["vízállás"], "targetBlank": False, "title": "menü", "tooltip": "", "type": "dashboards", "url": ""}],
        "templating": {"list": [
            variable("elso_ev", "Első év",
                     "SELECT min(extract(year FROM day_local))::int FROM daily_aggregates"
                     " WHERE station_tsz = 550"),
            variable("aktualis_ev", "Aktuális év", "SELECT extract(year FROM now())::int"),
            variable("elso_ev_bartal", "Első év (Bartal)",
                     "SELECT min(extract(year FROM day_local))::int FROM daily_aggregates"
                     " WHERE station_tsz = 142062"),
        ]},
        "time": {"from": "now/y", "to": "now/y"},
        "timepicker": {}, "timezone": "Europe/Budapest",
        "title": "Dombori vízállás - szezonális összevetés",
        "uid": "dombori-szezonalis", "weekStart": "monday",
    }


ORSZAGOS_STATIONS = (
    ("Komárom", 5, "442522H", 845),
    ("Budapest", 1026, "442027H", 891),
    ("Paks", 549, "442030H", 891),
    ("Mohács", 831, "442032H", 984),
)


def build_orszagos() -> dict:
    base = build()
    panels = []
    for i, (name, tsz, code, lnv) in enumerate(ORSZAGOS_STATIONS):
        panels.append(panel(
            pid=i + 1, tsz=tsz, y=i * 13, first_year_var=1901, decades=True,
            title=f"{name} - napi átlag évenként egymáson (${{elso_ev}}-${{aktualis_ev}})",
            description=f"Minden év az aktuális év naptárára vetítve - évfüggetlen."
                        f" Színskála: sötétszürke (régi) → kék (új) évtizedenként;"
                        f" narancs = tavalyi, vastag piros = idei; sötétpiros szaggatott ="
                        f" Hydroinfo előrejelzés. Piros vonal: LNV {lnv} cm.",
            thresholds=[{"color": "transparent", "value": 0}, {"color": "red", "value": lnv}],
            threshold_mode="line", run_filter=f"station_code = '{code}'",
            forecast=True, anchored=True))
    base["panels"] = panels
    base["annotations"]["list"] = base["annotations"]["list"][:1]
    base["templating"]["list"] = [
        variable("elso_ev", "Első év",
                 "SELECT min(extract(year FROM day_local))::int FROM daily_aggregates"
                 " WHERE station_tsz IN (5, 1026, 549, 831)"),
        variable("aktualis_ev", "Aktuális év", "SELECT extract(year FROM now())::int"),
    ]
    base["title"] = "Országos vízállás - szezonális összevetés"
    base["uid"] = "orszagos-vizallas-szezonalis"
    return base


if __name__ == "__main__":
    import sys
    dashboard = build_orszagos() if "orszagos" in sys.argv[1:] else build()
    print(json.dumps(dashboard, ensure_ascii=False, indent=2))
