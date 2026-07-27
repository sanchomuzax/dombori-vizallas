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

## Üzemeltetés

- DB: Docker `dombori_db` (postgres:17-alpine), named volume `dombori_pgdata`,
  port `127.0.0.1:5434`. a host alapértelmezett Postgres-portjai másra foglaltak,
  ezért az eltérő port.
- Grafana read-only role: `dombori_ro` — jelszó a `.env.local`-ban
  (`DOMBORI_RO_PASSWORD`); az `init-db` idempotensen (újra)beállítja.
- psycopg3 `executemany` **kumulatív** rowcountot ad — az upsert-számlálók
  erre építenek (a `WHERE ... IS DISTINCT FROM` guard miatt a változatlan
  sorok nem számítódnak).
- Backfill: folytatható (`backfill_state` checkpoint), chunkonként
  `ingestion_runs` bejegyzés; 2 üres 10 éves chunk után áll le.
- Volumen a teljes betöltés után: ~64k napi aggregátum-sor + gördülő ~5.4k
  15 perces sor — elhanyagolható.
