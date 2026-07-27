-- Dombori vízállás schema (idempotent)

CREATE TABLE IF NOT EXISTS stations (
  tsz         BIGINT PRIMARY KEY,           -- 550 (Duna-Dombori), 142062 (Fadd/Bartal)
  name        TEXT NOT NULL,
  fkm         NUMERIC(6,1),
  npt_mbf     NUMERIC(6,2),                 -- gauge zero point, mBf
  lkv_cm      INTEGER,
  lnv_cm      INTEGER,
  kf1_cm      INTEGER,
  kf2_cm      INTEGER,
  kf3_cm      INTEGER,
  lat         DOUBLE PRECISION,
  lon         DOUBLE PRECISION,
  metadata    JSONB NOT NULL DEFAULT '{}',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS observations (
  station_tsz BIGINT NOT NULL REFERENCES stations(tsz),
  ts_utc      TIMESTAMPTZ NOT NULL,
  value_cm    NUMERIC(7,1) NOT NULL,
  inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (station_tsz, ts_utc)
);
CREATE INDEX IF NOT EXISTS idx_observations_ts ON observations (ts_utc);

CREATE TABLE IF NOT EXISTS daily_aggregates (
  station_tsz  BIGINT NOT NULL REFERENCES stations(tsz),
  day_local    DATE NOT NULL,               -- Europe/Budapest calendar day
  min_cm       NUMERIC(7,1) NOT NULL,
  max_cm       NUMERIC(7,1) NOT NULL,
  mean_cm      NUMERIC(7,1) NOT NULL,
  sample_count INTEGER NOT NULL,
  computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (station_tsz, day_local)
);

CREATE TABLE IF NOT EXISTS forecast_runs (
  id           BIGSERIAL PRIMARY KEY,
  station_code TEXT NOT NULL,               -- '442540H'
  issue_ts     TIMESTAMPTZ NOT NULL,        -- "Kiadva: ..." timestamp
  content_hash TEXT NOT NULL,               -- sha256 of raw HTML bytes
  etag         TEXT,
  source       TEXT NOT NULL CHECK (source IN ('table', 'imagemap')),
  raw_path     TEXT NOT NULL,               -- relative path under DOMBORI_DATA_DIR
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (station_code, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_forecast_runs_issue
  ON forecast_runs (station_code, issue_ts DESC);

CREATE TABLE IF NOT EXISTS forecast_points (
  run_id        BIGINT NOT NULL REFERENCES forecast_runs(id) ON DELETE CASCADE,
  target_ts     TIMESTAMPTZ NOT NULL,
  value_cm      NUMERIC(7,1) NOT NULL,
  error_band_cm NUMERIC(6,1),               -- ± band, forecast points only
  point_type    TEXT NOT NULL CHECK (point_type IN ('observed', 'forecast')),
  PRIMARY KEY (run_id, target_ts, point_type)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
  id            BIGSERIAL PRIMARY KEY,
  job           TEXT NOT NULL,              -- collect | hydroinfo | daily | backfill
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ,
  status        TEXT NOT NULL DEFAULT 'running',  -- running | ok | error | noop
  rows_upserted INTEGER NOT NULL DEFAULT 0,
  detail        JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_job
  ON ingestion_runs (job, started_at DESC);

CREATE TABLE IF NOT EXISTS backfill_state (
  station_tsz   BIGINT PRIMARY KEY REFERENCES stations(tsz),
  next_end_date DATE NOT NULL,              -- backfill walks backwards; next chunk ends here
  done          BOOLEAN NOT NULL DEFAULT false,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
