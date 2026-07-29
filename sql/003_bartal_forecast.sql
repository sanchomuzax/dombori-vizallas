-- Statisztikai Bartal-előrejelzés támogatása (idempotens).

ALTER TABLE forecast_runs DROP CONSTRAINT IF EXISTS forecast_runs_source_check;
ALTER TABLE forecast_runs ADD CONSTRAINT forecast_runs_source_check
  CHECK (source IN ('table', 'imagemap', 'statisztikai'));

ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS detail JSONB NOT NULL DEFAULT '{}';
