"""Tests for dombori.retention.prune_observations.

Two layers:

* Pure unit tests against a stub psycopg-like connection/cursor that just
  records the SQL and parameters passed to `.execute()` -- these run
  always, no I/O.
* A couple of `@pytest.mark.integration` tests against the real Postgres
  instance (127.0.0.1:5434 per docker-compose.yml), which skip cleanly if
  that database isn't reachable or isn't configured.
"""

from __future__ import annotations

import socket
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from dombori.retention import _PRUNE_COUNT_SQL, _PRUNE_SQL, prune_observations

_BUDAPEST_TZ = ZoneInfo("Europe/Budapest")


# ==========================================================================
# Pure unit tests: stub connection/cursor
# ==========================================================================


class StubCursor:
    """Records every `.execute()` call; returns a canned fetchone/rowcount."""

    def __init__(self, fetchone_result=None, rowcount=0):
        self.executed: list[tuple[str, tuple]] = []
        self._fetchone_result = fetchone_result
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class StubConnection:
    def __init__(self, cursor: StubCursor):
        self._cursor = cursor
        self.commit_calls = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commit_calls += 1


def test_prune_observations_dry_run_issues_select_count():
    cur = StubCursor(fetchone_result=(5,))
    conn = StubConnection(cur)

    result = prune_observations(conn, keep_days=28, dry_run=True)

    assert result == 5
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    assert sql is _PRUNE_COUNT_SQL
    assert sql is not _PRUNE_SQL
    assert params == (28,)
    assert conn.commit_calls == 1


def test_prune_observations_wet_run_issues_delete():
    cur = StubCursor(rowcount=7)
    conn = StubConnection(cur)

    result = prune_observations(conn, keep_days=14, dry_run=False)

    assert result == 7
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    assert sql is _PRUNE_SQL
    assert sql is not _PRUNE_COUNT_SQL
    assert params == (14,)
    assert conn.commit_calls == 1


def test_prune_observations_default_keep_days_is_28():
    cur = StubCursor(rowcount=0)
    conn = StubConnection(cur)

    prune_observations(conn)

    _, params = cur.executed[0]
    assert params == (28,)


def test_prune_observations_dry_run_with_no_rows_returns_zero():
    cur = StubCursor(fetchone_result=None)
    conn = StubConnection(cur)

    result = prune_observations(conn, keep_days=28, dry_run=True)

    assert result == 0


def test_prune_observations_dry_run_never_deletes():
    """Sanity guard: the SQL text used for dry_run must be a SELECT, and the
    wet-run SQL must be a DELETE -- catches an accidental swap of the two
    module-level constants."""
    assert _PRUNE_COUNT_SQL.strip().upper().startswith("SELECT")
    assert _PRUNE_SQL.strip().upper().startswith("DELETE")


# ==========================================================================
# Integration tests: real DB at 127.0.0.1:5434 (skip cleanly if unreachable)
# ==========================================================================

_TEST_TSZ_NO_AGGREGATE = 999999
_TEST_TSZ_WITH_AGGREGATE = 999998


def _db_reachable(host: str, port: int, timeout: float = 0.75) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def db_conn():
    import psycopg

    from dombori.config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError:
        pytest.skip("DOMBORI_DB_* config not available (.env.local missing)")
        return

    if not _db_reachable(cfg.db_host, cfg.db_port):
        pytest.skip(f"DB not reachable at {cfg.db_host}:{cfg.db_port}")

    try:
        conn = psycopg.connect(
            host=cfg.db_host,
            port=cfg.db_port,
            dbname=cfg.db_name,
            user=cfg.db_user,
            password=cfg.db_password,
            connect_timeout=2,
        )
    except psycopg.OperationalError as exc:
        pytest.skip(f"Could not connect to DB: {exc}")
        return

    try:
        yield conn
    finally:
        conn.close()


def _cleanup_test_station(conn, tsz: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM observations WHERE station_tsz = %s", (tsz,))
        cur.execute("DELETE FROM daily_aggregates WHERE station_tsz = %s", (tsz,))
        cur.execute("DELETE FROM stations WHERE tsz = %s", (tsz,))
    conn.commit()


@pytest.mark.integration
def test_prune_observations_skips_row_without_aggregate(db_conn):
    tsz = _TEST_TSZ_NO_AGGREGATE
    old_ts_utc = datetime.now(timezone.utc) - timedelta(days=40)

    _cleanup_test_station(db_conn, tsz)
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stations (tsz, name) VALUES (%s, %s)",
                (tsz, "TEST STATION (no aggregate)"),
            )
            cur.execute(
                "INSERT INTO observations (station_tsz, ts_utc, value_cm) "
                "VALUES (%s, %s, %s)",
                (tsz, old_ts_utc, -50.0),
            )
        db_conn.commit()

        # No daily_aggregates row exists for this station/day -> must not prune.
        prune_observations(db_conn, keep_days=28, dry_run=False)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM observations WHERE station_tsz = %s", (tsz,)
            )
            (count,) = cur.fetchone()
        assert count == 1
    finally:
        _cleanup_test_station(db_conn, tsz)


@pytest.mark.integration
def test_prune_observations_deletes_row_with_fresh_aggregate(db_conn):
    tsz = _TEST_TSZ_WITH_AGGREGATE
    old_ts_utc = datetime.now(timezone.utc) - timedelta(days=40)
    day_local = old_ts_utc.astimezone(_BUDAPEST_TZ).date()

    _cleanup_test_station(db_conn, tsz)
    try:
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO stations (tsz, name) VALUES (%s, %s)",
                (tsz, "TEST STATION (with aggregate)"),
            )
            cur.execute(
                "INSERT INTO observations (station_tsz, ts_utc, value_cm) "
                "VALUES (%s, %s, %s)",
                (tsz, old_ts_utc, -50.0),
            )
        db_conn.commit()

        # Aggregate row computed_at defaults to now(), which is after the
        # observation's updated_at (inserted just above) -> eligible for prune.
        with db_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO daily_aggregates "
                "(station_tsz, day_local, min_cm, max_cm, mean_cm, sample_count) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (tsz, day_local, -50.0, -50.0, -50.0, 1),
            )
        db_conn.commit()

        prune_observations(db_conn, keep_days=28, dry_run=False)

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM observations WHERE station_tsz = %s", (tsz,)
            )
            (count,) = cur.fetchone()
        assert count == 0
    finally:
        _cleanup_test_station(db_conn, tsz)
