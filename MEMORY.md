# MEMORY.md — technikai tudásbázis

Tények, amiket a kód nem mond el magától. Frissítsd, ha újat tanulsz!

## API-tények (vraquery, 2026-07-27-én verifikálva)

- Token: `GET https://data.vizugy.hu/AuthApi/auth/token` — **csak**
  `Origin: https://data.vizugy.hu` és `Referer: https://data.vizugy.hu/`
  fejléccel megy (különben 403). Rövid életű JWT, `exp` claim alapján frissítünk.
- Idősor: `POST https://vmservice.vizugy.hu/vraquery/TS/TsShortList`
  Bearer tokennel. Body-minta a `vizugy_client.py`-ban. `adatFajtaKod=68` =
  felszíni vízállás (cm, "Relativ" = vízmérce-relatív).
- **Az állomáslista endpoint (`Vra/InternetVmo/11/false`) is Bearer tokent
  kér** — a 2026-07-27-i kutatási jelentés "no auth" állítása a gyakorlatban
  nem állt meg (401).
- Ablakméret: gyakorlatilag korlátlan — 100 éves kérés is <1 s alatt
  kiszolgálódik. Évhatár-probléma nincs.
- Válasz UTC-ben (`UTCTime` ...Z); a kérés start/end viszont HELYI idő.

## Adatmélység és granularitás

- Duna–Dombori (550): **1901-01-01-től**; Bartal/Fadd (142062): **1973-06-07-től**.
- Történelmi adat napi 1–2 észlelés (kb. 06/07h és 15h UTC); a modern kor 15 perces.
- Rekord a betöltött adatban: **1956-03-12/13: 1077 cm** (jeges árvíz) — magasabb,
  mint a nyilvántartott LNV (916 cm, ami a 2013-06-11/12-es tetőzés).
- A 15 perces sorban ritkán 30 perces lyukak vannak — a collect 24 órás
  csúszóablaka ezeket utólag pótolja.

## Hydroinfo furcsaságok

- A `tables/442540H.html` fejlécében a hónapnév **duplán escape-elt, hibás
  HTML-entitás**: `j&amp;uacutelius` → kétszeri `html.unescape` kell.
- A táblában az órák vezető nullásak (`01:00`), az imagemap-fallbackben
  (`elore.php?all=442540H`) viszont nem (`1:00`).
- Napszak-címkék → helyi óra: éjjel=01, reggel=07, délben=13, este=19.
- A tábla-oldalon VAN `ETag` + `Last-Modified` (conditional GET működik);
  az imagemap-oldalon NINCS (ott csak content-hash).
- Az imagemap-oldal bájtjai valójában UTF-8-ak a deklarált ISO-8859-2 helyett
  → `hydroinfo._fix_mojibake()` kezeli.
- WAF: sima requests default UA-val működik; böngésző-UA spoofolás blokkolva.
  Óránkénti gyakoriság fölé ne menjünk.
- Kiadás kb. napi 4× (ECMWF-futásokhoz kötve); a kiadási idő (`Kiadva:`) a
  forecast_runs kulcs része.

## Grafana dashboardok (uid: `dombori-vizallas`, `dombori-szezonalis`)

- **Epoch-csapda**: a `$__timeGroup` makró numerikus epochot ad vissza, és a
  Grafana a ±10⁹ alatti értékeket (kb. 1938–2001) milliszekundumnak nézi →
  az idősor 1970-re esik össze. Megoldás: kézi bucketolás
  `to_timestamp(floor(extract(epoch FROM ...) / N) * N)`-nel (valódi
  timestamptz-t adunk vissza). A történelmi sáv-panelek így működnek.
- **Adaptív felbontás**: a bucket-méret `GREATEST($__interval_ms/1000, 86400)`
  — széles nézetben havi, bezoomolva napi.
- **Trend panel korlátai**: csak EGY frame-et fogad (partition után joinByField
  kell), és a tengelyére csak számot tud írni, dátumot/hónapnevet nem.
  Szezonális (évek egymáson) nézethez ezért külön dashboard van:
  minden év az AKTUÁLIS évre vetítve (`make_date(extract(year FROM now())...)`),
  fix `now/y → now/y` időablakkal → valódi hónapnevek a tengelyen.
- **Évfüggetlenség**: az idei/tavalyi kiemelés a metric NEVÉBEN van
  (`'idei (2026)'`, `'tavalyi (2025)'` — CASE a SQL-ben), a stílus
  `^idei.*`/`^tavalyi.*` regex-override; a panelcím évszámai rejtett
  template-változók (`elso_ev`, `elso_ev_bartal`, `aktualis_ev`). Semmi
  beégetett évszám!
- **Gradiens színezés**: évenkénti byRegexp override-ok (`^1901$`…),
  sötétszürke→kék; 2027–2035 előre beszínezve. A legendából a történelmi évek
  `defaults.custom.hideFrom.legend=true`-val vannak kirejtve, a kiemelt sorok
  override-ban kapják vissza.
- **Küszöbvonalak**: `thresholdsStyle: line` + színes step-ek — Duna: 400 cm
  szivornya-minimum (zöld), LKV −87 / LNV 916 (piros); Bartal: KF 170/190/200.
- **Archív folytonosság**: a 15 perces panelek 28 napnál régebbre a napi
  átlaggal folytatódnak UNION ALL-lal, azonos series-névvel (délre igazítva).
- A user maga is szerkeszti a dashboardokat a UI-ban — MCP-ből CSAK célzott
  patch-műveletet (`operations`) használj, teljes felülírást soha, és a
  színeit/elrendezését ne írd felül!
- **Esemény-annotációk**: `holtag_events` tábla (seed `sql/002_events.sql`,
  csak vízállás-releváns események, `impact` pozitiv/negativ) → a fő
  dashboardon két annotáció-réteg (zöld/piros), SQL-lel:
  `start_date::timestamptz AS time, end_date::timestamptz AS timeend` —
  itt is timestamptz kell, numerikus epoch NEM (ld. epoch-csapda fent).

## Üzemeltetés

- DB: Docker `dombori_db` (postgres:17-alpine), named volume `dombori_pgdata`,
  port `127.0.0.1:5434`. A gépen a 5432 (n8n), 5433 (natív PG17), 15434
  (CodexClaw) már foglalt.
- Grafana read-only role: `dombori_ro` — jelszó a `.env.local`-ban
  (`DOMBORI_RO_PASSWORD`); az `init-db` idempotensen (újra)beállítja.
- psycopg3 `executemany` **kumulatív** rowcountot ad — az upsert-számlálók
  erre építenek (a `WHERE ... IS DISTINCT FROM` guard miatt a változatlan
  sorok nem számítódnak).
- Backfill: folytatható (`backfill_state` checkpoint), chunkonként
  `ingestion_runs` bejegyzés; 2 üres 10 éves chunk után áll le.
- Volumen a teljes betöltés után: ~64k napi aggregátum-sor + gördülő ~5.4k
  15 perces sor — elhanyagolható.
