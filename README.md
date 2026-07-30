# Dombori vízállás

Dunai vízállás-gyűjtő és -megjelenítő rendszer a Dombori környéki vízmércékhez:
folyamatos 15 perces gyűjtés, 125 évre visszanyúló történelmi adatbázis, napi
aggregálás, Hydroinfo-előrejelzések verziózott tárolása, Grafana dashboard.

## Állomások

| Állomás | Törzsszám | Meder | Jellemzők |
|---|---|---|---|
| Duna – Dombori | `550` | Duna, 1506,8 fkm | Npt 83,52 mBf; LKV −87 cm; LNV 916 cm; adatok **1901-től** |
| Fadd (Dombori), volt Bartal zsilip | `142062` | Faddi-Holt-Duna | Npt 86,53 mBf; LKV 35; LNV 208; KF 170/190/200 cm; adatok **1973-tól** |

## Adatforrások

- **vraquery API** (a [data.vizugy.hu](https://data.vizugy.hu) backendje) — mért
  vízállás, 15 perces bontásban. Token: `GET /AuthApi/auth/token` (Origin/Referer
  fejléc kötelező), idősor: `POST /vraquery/TS/TsShortList` Bearer tokennel.
- **Hydroinfo** ([hydroinfo.hu](https://www.hydroinfo.hu)) — 6 órás
  vízállás-előrejelzés a `442540H` (Duna–Dombori) állomásra, óránkénti
  conditional GET-tel (ETag), kiadásonként verziózva.

Az adatok az OVF nyílt adat politikája szerint forrásmegjelöléssel
felhasználhatók; a legutóbbi ~1 év adatai nem minőségbiztosítottak.

## Architektúra

```
systemd user timerek
  ├─ dombori-collect   (15 percenként)  → vraquery → observations
  ├─ dombori-hydroinfo (óránként)       → hydroinfo → forecast_runs/points + raw gzip
  └─ dombori-daily     (03:30)          → daily_aggregates upsert + retenció
                                            │
                          PostgreSQL 17 (Docker, 127.0.0.1:5434)
                                            │
                          Grafana (localhost:3000, dombori_ro read-only role)
```

**Retenció:** a 15 perces adatpontok 28 napig maradnak meg; régebbi napokból
napi aggregátum (min/max/átlag/darabszám) őrződik meg örökre. Törlés csak akkor
történik, ha a naphoz létezik az utolsó adatváltozásnál frissebb aggregátum.

## Telepítés

```bash
cp .env.local.example .env.local   # jelszavak kitöltése
chmod 600 .env.local
docker compose --env-file .env.local up -d
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m dombori init-db     # séma + állomás-seed + dombori_ro role
.venv/bin/python -m dombori backfill    # teljes történelmi betöltés (~1 perc)
./scripts/install_systemd.sh            # timerek telepítése és indítása
```

## CLI

```bash
.venv/bin/python -m dombori init-db            # séma, seed, read-only role
.venv/bin/python -m dombori collect            # utolsó 24 h lekérése + upsert
.venv/bin/python -m dombori hydroinfo          # előrejelzés-snapshot (ETag/noop)
.venv/bin/python -m dombori daily [--dry-run]  # aggregálás + retenció
.venv/bin/python -m dombori backfill [--station TSZ] [--chunk-years N]
```

## Statisztikai Bartal-előrejelzés

A `daily` job (és a kézi `bartal-forecast` parancs) 6 napos előrejelzést számol
a Bartal (142062) állomásra: **perzisztencia + szezonális drift** (a 2005 utáni
napi adatok naptári-nap-ablakos mediánja) + **empirikus p5–p95 hibasáv**.
A Duna-szint korrelációja mérten elhanyagolható (Δ-korreláció ≈ 0, a holtág
zsilipekkel kezelt rendszer), ezért a Duna csak eseményjelző: ha a Hydroinfo
Duna-előrejelzés eléri a 400 cm-t, a run `szivornya_lehetseges` jelzést kap.
Az eredmény a `forecast_runs`/`forecast_points` táblákba kerül
(`source='statisztikai'`), a dashboardon saját panel + szivornya-stat mutatja.

## Történeti esemény-annotációk

A `holtag_events` tábla (seed: `sql/002_events.sql`) a holtág vízállás
szempontjából releváns történeti eseményeit tartalmazza (1838-tól), `impact`
minősítéssel. A fő Grafana dashboardon annotációként jelennek meg: **zöld** =
pozitív (vízpótlás, kotrás, duzzasztás), **piros** = negatív (lefűződés,
árvíz, vízminőségi kár). A két réteg a dashboard tetején ki-be kapcsolható.
Új esemény felvétele: sor a seed SQL-be, majd újrafuttatás
(`docker exec -i dombori_db psql -U dombori -d dombori < sql/002_events.sql`).

## Grafana dashboardok (verziózva)

A `grafana/` mappa a két dashboard mentett definícióját tartalmazza. Titkot nem
tartalmaznak: a datasource-ra a `${DS_DOMBORI}` placeholderrel hivatkoznak
(importáláskor a Grafana megkérdezi, melyik adatforrás legyen), a `dombori_ro`
jelszava a Grafana saját titkos tárában marad.

| Fájl | Tartalom |
|---|---|
| `dombori-vizallas.json` | fő dashboard (aktuális szintek, előrejelzések, kamerák, történelmi sávok, gyűjtés-állapot) |
| `dombori-szezonalis.json` | szezonális összevetés (évek egymáson) — **generált**, ne kézzel szerkeszd |
| `build_szezonalis.py` | a szezonális dashboard generátora (~200 évenkénti szín-override) |

Frissítés: `python3 grafana/build_szezonalis.py > grafana/dombori-szezonalis.json`
Importálás: Grafana → Dashboards → New → Import → *Upload JSON file*.

## Tesztek

```bash
.venv/bin/python -m pytest tests/ -q
```

Az integrációs tesztek (élő DB kell hozzájuk) automatikusan kimaradnak, ha a
`127.0.0.1:5434` nem elérhető.

## Üzemeltetés

- Logok: `journalctl --user -u dombori-collect` (ill. `-hydroinfo`, `-daily`)
- Ingestion napló a DB-ben: `ingestion_runs` tábla (status: ok/error/noop)
- Backup: `./scripts/db_backup.sh` (pg_dump, 14 napos rotáció) — az éjszakai
  host-backupba egy sorral beköthető
- Grafana datasource: PostgreSQL, `localhost:5434`, db `dombori`, user
  `dombori_ro` (jelszó: `.env.local` → `DOMBORI_RO_PASSWORD`)
