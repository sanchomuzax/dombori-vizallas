# CLAUDE.md - dombori-vizallas

Útmutató AI agenteknek ehhez a repóhoz.

## Mi ez?

Dunai vízállás-gyűjtő (Dombori környéke): vraquery API + Hydroinfo →
PostgreSQL (Docker, `127.0.0.1:5434`) → Grafana. Részletek: `README.md`,
technikai tudásbázis: `MEMORY.md`.

## Szabályok

- **Commit/push/release CSAK a felhasználó kifejezett kérésére.**
- **Titok nem kerülhet commitolt fájlba**: jelszó, token, API-kulcs tilos;
  minden a `.env.local`-ból jön (gitignore-olva). A `.env.local`-t soha ne
  írd ki, ne logold, ne commitold.
- **Commitolt fájlban nem szerepelhet** `/home/<user>` abszolút út vagy
  felhasználónév - systemd unitokban `%h`, scriptekben `$HOME` / `BASH_SOURCE`.
- Érdemi változásnál frissítsd a `README.md` + `MEMORY.md` fájlokat is.
- Verzióemeléskor: verzió átvezetése (`pyproject.toml`, `src/dombori/__init__.py`),
  git tag `vX.Y.Z`, `gh release create` - kötelező.

## Kódstílus

- Python 3.13, csak a `.venv` (telepítés: `pip install -e '.[dev]'`).
- Immutabilitás: frozen dataclass-ok, nincs argumentum-mutálás.
- Kis fájlok (<400 sor), kis függvények (<50 sor), tipizált kivételek,
  defenzív input-validálás minden külső adatra (API JSON, HTML).
- Diagnosztika a `logging` modullal (stderr), nem `print()`-tel.

## Gyakori műveletek

```bash
.venv/bin/python -m pytest tests/ -q                 # tesztek
.venv/bin/python -m dombori collect                  # kézi gyűjtés
.venv/bin/python -m dombori daily --dry-run          # retenció előnézet
docker compose --env-file .env.local up -d           # DB indítás
journalctl --user -u dombori-collect -n 20           # timer log
```

## Felépítés

- `src/dombori/` - a package; belépési pont `__main__.py` (argparse subcommandok)
- `sql/001_schema.sql` - idempotens DDL; sémaváltozás ide, új számozott fájlként
- `tests/fixtures/` - élőből mentett HTML/JSON minták (parserek ehhez kötve)
- `systemd/` - a unitok forrásai; telepítés `scripts/install_systemd.sh`-val
- `data/` - gitignore-olt futásidejű adatok (raw előrejelzés-snapshotok)
