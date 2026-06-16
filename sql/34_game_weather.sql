-- ════════════════════════════════════════════════════════════════════════
-- 34_game_weather.sql  ·  v2 rebuild Day 3 — per-game weather snapshots
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   New table game_weather: multi-snapshot weather history per game, with a
--   computed weather_hr_index (MULTIPLIER scale, ~0.6-1.5, 1.000 = neutral).
--   Two snapshots per game:
--     'cron_330am'              — pick-generation-time forecast (3:30 AM ET)
--     '1h_before_first_pitch'   — refreshed forecast caught by the hourly cron
--   The v2 HR model (Day 5) reads the latest available snapshot via
--   scripts/v2/weather_lookup.get_weather_hr_index().
--
-- SOURCE / COMPUTE
--   Open-Meteo forecast (no key). weather_hr_index is computed by
--   scripts/v2/weather_index.compute_weather_hr_index() from temp / humidity /
--   wind (direction rotated into the home-plate→CF axis via
--   teams.home_plate_bearing) and roof_status. Coords + bearing come from the
--   teams table; roof type from scripts/v2/ballparks.ROOF_TYPE.
--
-- COEXISTENCE
--   Distinct from the legacy single-snapshot weather columns on `games`
--   (temp_f/wind_*/hr_factor_*), which pull_weather.py keeps writing for the
--   v0.1.3 model. Both preserved — same pattern as park_factors vs
--   teams.park_hr_*.
--
--   CREATE TABLE IF NOT EXISTS → IDEMPOTENT, safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS game_weather (
    id                 SERIAL PRIMARY KEY,
    game_id            INTEGER REFERENCES games(id),
    fetched_at         TIMESTAMPTZ DEFAULT NOW(),
    snapshot_type      TEXT NOT NULL,           -- 'cron_330am' | '1h_before_first_pitch'
    temp_f             NUMERIC(5, 1),
    humidity_pct       NUMERIC(5, 2),
    wind_mph           NUMERIC(5, 1),
    wind_direction_deg INTEGER,                 -- 0-360, direction wind blows FROM (raw Open-Meteo)
    cloud_cover_pct    NUMERIC(5, 2),           -- reserved (not fetched/used yet)
    precipitation_pct  NUMERIC(5, 2),           -- precipitation probability (future use)
    roof_status        TEXT,                    -- 'no_roof' | 'retractable_uncertain' | 'closed'
    weather_hr_index   NUMERIC(5, 3),           -- multiplier, clamped [0.600, 1.500]; 1.000 = neutral
    source             TEXT,                    -- 'open_meteo'
    UNIQUE (game_id, snapshot_type)
);

CREATE INDEX IF NOT EXISTS idx_game_weather_game ON game_weather(game_id);

COMMENT ON TABLE  game_weather IS
  'Per-game weather snapshots (Open-Meteo) + computed weather_hr_index (multiplier, 1.000 neutral). v2 HR model input (Day 3). Distinct from legacy games weather columns.';
COMMENT ON COLUMN game_weather.snapshot_type IS
  'Which fetch this is: cron_330am (pick time) or 1h_before_first_pitch (hourly-cron refresh). Part of the upsert uniqueness key.';
COMMENT ON COLUMN game_weather.wind_direction_deg IS
  'Compass degrees (0-360) the wind blows FROM (Open-Meteo convention). Rotated to TOWARD (+180) before the field-relative HR calc.';
COMMENT ON COLUMN game_weather.roof_status IS
  'Derived per game from scripts/v2/ballparks.ROOF_TYPE: open->no_roof (full weather), fixed->closed (index 1.000), retractable->retractable_uncertain (50% weather discount).';
COMMENT ON COLUMN game_weather.weather_hr_index IS
  'Weather-only HR multiplier (100=avg expressed as 1.000), clamped [0.600,1.500]. compute_weather_hr_index(). v2 converts park/weather/etc. separately.';
COMMENT ON COLUMN game_weather.cloud_cover_pct IS
  'Reserved column — cloud cover is not fetched or used in the index yet (Day 3 decision).';
COMMENT ON COLUMN game_weather.precipitation_pct IS
  'Precipitation probability %. Stored for future use; not in the current index formula.';
