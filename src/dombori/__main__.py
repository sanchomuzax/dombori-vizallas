"""CLI entry point: `python -m dombori <subcommand>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from psycopg import sql

from dombori import aggregate, backfill, db, forecasts, hydroinfo, retention, runs, vizugy_client
from dombori.config import Config, ConfigError, load_config
from dombori.observations import parse_timeseries, upsert_observations

logger = logging.getLogger(__name__)

_BUDAPEST_TZ = ZoneInfo("Europe/Budapest")
_STATIONS = (550, 142062)
_SCHEMA_PATH = "sql/001_schema.sql"
_RO_ROLE_NAME = "dombori_ro"

_STATION_FIELD_MAP = {
    "name": "Nev",
    "fkm": "Fkm",
    "npt_mbf": "Npt",
    "lkv_cm": "LKV",
    "lnv_cm": "LNV",
    "kf1_cm": "KF1",
    "kf2_cm": "KF2",
    "kf3_cm": "KF3",
    "lat": "Lat",
    "lon": "Lon",
}

_STATION_UPSERT_SQL = """
    INSERT INTO stations
        (tsz, name, fkm, npt_mbf, lkv_cm, lnv_cm, kf1_cm, kf2_cm, kf3_cm, lat, lon,
         metadata, updated_at)
    VALUES (%(tsz)s, %(name)s, %(fkm)s, %(npt_mbf)s, %(lkv_cm)s, %(lnv_cm)s,
            %(kf1_cm)s, %(kf2_cm)s, %(kf3_cm)s, %(lat)s, %(lon)s, %(metadata)s::jsonb, now())
    ON CONFLICT (tsz) DO UPDATE SET
        name = EXCLUDED.name, fkm = EXCLUDED.fkm, npt_mbf = EXCLUDED.npt_mbf,
        lkv_cm = EXCLUDED.lkv_cm, lnv_cm = EXCLUDED.lnv_cm, kf1_cm = EXCLUDED.kf1_cm,
        kf2_cm = EXCLUDED.kf2_cm, kf3_cm = EXCLUDED.kf3_cm, lat = EXCLUDED.lat,
        lon = EXCLUDED.lon, metadata = EXCLUDED.metadata, updated_at = now()
"""


def _seed_stations(conn, session: requests.Session) -> int:
    token = vizugy_client.fetch_token(session)
    records = vizugy_client.fetch_stations(session, token)
    seeded = 0
    for record in records:
        if not isinstance(record, dict) or "Tsz" not in record:
            continue
        try:
            tsz = int(record["Tsz"])
        except (TypeError, ValueError):
            continue
        if tsz not in _STATIONS:
            continue

        params = {"tsz": tsz, "metadata": json.dumps(record)}
        for column, source_key in _STATION_FIELD_MAP.items():
            params[column] = record.get(source_key)

        with conn.cursor() as cur:
            cur.execute(_STATION_UPSERT_SQL, params)
        seeded += 1

    conn.commit()
    return seeded


def _configure_ro_role(conn, config: Config) -> None:
    """(Re)create the read-only `dombori_ro` role and grant it SELECT.

    DDL statements can't bind parameters, so the password is safely composed
    with psycopg.sql.Literal (which quotes/escapes it) rather than an
    f-string -- never interpolate untrusted or secret strings into SQL text
    directly.
    """
    role = sql.Identifier(_RO_ROLE_NAME)
    password = sql.Literal(config.ro_password)
    database = sql.Identifier(config.db_name)

    statements = [
        sql.SQL(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {role_name}) THEN "
            "CREATE ROLE {role} LOGIN PASSWORD {password}; "
            "END IF; END $$;"
        ).format(role_name=sql.Literal(_RO_ROLE_NAME), role=role, password=password),
        sql.SQL("ALTER ROLE {role} WITH LOGIN PASSWORD {password}").format(
            role=role, password=password
        ),
        sql.SQL("GRANT CONNECT ON DATABASE {database} TO {role}").format(
            database=database, role=role
        ),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {role}").format(role=role),
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}").format(role=role),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role}"
        ).format(role=role),
    ]

    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def cmd_init_db(conn, config: Config, _args: argparse.Namespace) -> int:
    db.execute_sql_file(conn, _SCHEMA_PATH)
    logger.info("Applied schema from %s", _SCHEMA_PATH)

    session = requests.Session()
    seeded = _seed_stations(conn, session)
    logger.info("Seeded %d station row(s)", seeded)

    if config.ro_password:
        _configure_ro_role(conn, config)
        logger.info("Configured read-only role %s", _RO_ROLE_NAME)
    else:
        logger.info("DOMBORI_RO_PASSWORD not set; skipping %s role setup", _RO_ROLE_NAME)

    return 0


def cmd_collect(conn, config: Config, _args: argparse.Namespace) -> int:
    del config
    run_id = runs.start_run(conn, "collect")
    try:
        now_utc = datetime.now(timezone.utc)
        start_local = (now_utc - timedelta(hours=24)).astimezone(_BUDAPEST_TZ).replace(tzinfo=None)
        end_local = (now_utc + timedelta(hours=1)).astimezone(_BUDAPEST_TZ).replace(tzinfo=None)

        session = requests.Session()
        token = vizugy_client.fetch_token(session)
        payload = vizugy_client.fetch_timeseries(
            session, token, list(_STATIONS), start_local, end_local
        )
        observations = parse_timeseries(payload)
        rows_upserted = upsert_observations(conn, observations)
        conn.commit()

        runs.finish_run(
            conn,
            run_id,
            "ok",
            rows_upserted=rows_upserted,
            detail={"window": [start_local.isoformat(), end_local.isoformat()]},
        )
        return 0
    except Exception as exc:
        conn.rollback()
        runs.finish_run(conn, run_id, "error", detail={"error": str(exc)})
        logger.error("collect job failed: %s", exc)
        raise


def cmd_hydroinfo(conn, config: Config, _args: argparse.Namespace) -> int:
    run_id = runs.start_run(conn, "hydroinfo")
    try:
        session = requests.Session()
        etag = forecasts.latest_etag(conn, hydroinfo.STATION_CODE)
        result = hydroinfo.fetch_table(session, etag=etag)

        if result.status == 304:
            runs.finish_run(conn, run_id, "noop", detail={"reason": "not modified"})
            logger.info("Hydroinfo table page unchanged (304)")
            return 0

        source = "table"
        raw = result.raw
        etag_out = result.etag
        try:
            issue_ts = hydroinfo.parse_issue_ts(result.text)
            points = hydroinfo.parse_table(result.text, issue_ts)
        except hydroinfo.ParseError as exc:
            logger.warning("Table parse failed (%s); falling back to imagemap", exc)
            source = "imagemap"
            fallback = hydroinfo.fetch_imagemap(session)
            raw = fallback.raw
            etag_out = None
            issue_ts = datetime.now(timezone.utc)
            points = hydroinfo.parse_imagemap(fallback.text)

        new_run_id = forecasts.store_run(
            conn, config, hydroinfo.STATION_CODE, issue_ts, raw, etag_out, source, points
        )

        if new_run_id is None:
            runs.finish_run(conn, run_id, "noop", detail={"reason": "unchanged content", "source": source})
        else:
            runs.finish_run(
                conn,
                run_id,
                "ok",
                rows_upserted=len(points),
                detail={"source": source, "forecast_run_id": new_run_id, "points": len(points)},
            )
        return 0
    except Exception as exc:
        conn.rollback()
        runs.finish_run(conn, run_id, "error", detail={"error": str(exc)})
        logger.error("hydroinfo job failed: %s", exc)
        raise


def cmd_daily(conn, config: Config, args: argparse.Namespace) -> int:
    del config
    run_id = runs.start_run(conn, "daily")
    try:
        aggregated = aggregate.upsert_daily_aggregates(conn, lookback_days=35)
        pruned = retention.prune_observations(conn, keep_days=28, dry_run=args.dry_run)
        runs.finish_run(
            conn,
            run_id,
            "ok",
            detail={"aggregated": aggregated, "pruned": pruned, "dry_run": args.dry_run},
        )
        return 0
    except Exception as exc:
        conn.rollback()
        runs.finish_run(conn, run_id, "error", detail={"error": str(exc)})
        logger.error("daily job failed: %s", exc)
        raise


def cmd_backfill(conn, config: Config, args: argparse.Namespace) -> int:
    tsz_list = (args.station,) if args.station else _STATIONS
    try:
        backfill.run_backfill(
            conn,
            config,
            tsz_list=tsz_list,
            chunk_years=args.chunk_years,
            pause_seconds=args.pause,
        )
        return 0
    except Exception as exc:
        logger.error("backfill job failed: %s", exc)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dombori", description="Dombori water-level collector")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Apply schema, seed stations, configure RO role")
    subparsers.add_parser("collect", help="Fetch recent observations from vizugy.hu")
    subparsers.add_parser("hydroinfo", help="Fetch and store the hydroinfo.hu forecast")

    daily_parser = subparsers.add_parser("daily", help="Recompute daily aggregates and prune raw data")
    daily_parser.add_argument("--dry-run", action="store_true", help="Only count rows that would be pruned")

    backfill_parser = subparsers.add_parser("backfill", help="Backfill historical observations")
    backfill_parser.add_argument("--station", type=int, default=None, help="Backfill only this station tsz")
    backfill_parser.add_argument("--chunk-years", type=int, default=10, help="Years per backfill chunk")
    backfill_parser.add_argument("--pause", type=float, default=3.0, help="Seconds to sleep between requests")

    return parser


_HANDLERS = {
    "init-db": cmd_init_db,
    "collect": cmd_collect,
    "hydroinfo": cmd_hydroinfo,
    "daily": cmd_daily,
    "backfill": cmd_backfill,
}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = _build_parser().parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    handler = _HANDLERS[args.command]
    try:
        with db.connect(config) as conn:
            return handler(conn, config, args)
    except Exception as exc:  # noqa: BLE001 - top-level catch-all for exit code
        logger.error("%s command failed: %s", args.command, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
