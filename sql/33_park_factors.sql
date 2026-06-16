-- ════════════════════════════════════════════════════════════════════════
-- 33_park_factors.sql  ·  v2 rebuild Day 2 — Statcast park factors
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   New table park_factors: per-park HR factor (overall + handedness split)
--   plus a runs factor for context, sourced from Baseball Savant's Statcast
--   park-factors leaderboard (3-year rolling). Populated by
--   scripts/pull_park_factors.py (manual / on-demand — these barely move
--   intra-season). Consumed by the v2 HR model (Day 5) via
--   scripts/v2/park_factor_lookup.get_park_hr_factor().
--
-- SCALE
--   INDEX scale, 100 = league average (Savant native; e.g. Coors HR ≈ 105,
--   Dodger Stadium ≈ 127, PNC ≈ 76). This DIFFERS from the legacy
--   teams.park_hr_factor columns, which are on a MULTIPLIER scale (1.00 = avg)
--   and feed the old v0.1.3 model. Both coexist; the v2 model converts index
--   → multiplier (index / 100) when composing hr_score_v2.
--
-- MAPPING
--   Savant's main_team_id == teams.mlb_id (MLB StatsAPI ids); the pull script
--   maps that to our internal teams.id for the FK below. NOTE: Savant omits
--   parks lacking 3 years of venue history (currently the Athletics /
--   Sacramento and Rays / temp venue) — those simply get no row, and the
--   lookup helper falls back to a neutral 100.
--
--   CREATE TABLE IF NOT EXISTS → IDEMPOTENT, safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS park_factors (
    id            SERIAL PRIMARY KEY,
    team_id       INTEGER REFERENCES teams(id),    -- our internal id (via mlb_id map)
    season        INTEGER NOT NULL,                -- season the window is anchored to
    window_yrs    INTEGER NOT NULL,                -- rolling window length (3 default; 1 reserved)
    hr_factor     NUMERIC(5, 2),                   -- overall HR index (batSide=all), 100 = avg
    hr_factor_lhb NUMERIC(5, 2),                   -- LHB HR index (batSide=L)
    hr_factor_rhb NUMERIC(5, 2),                   -- RHB HR index (batSide=R)
    runs_factor   NUMERIC(5, 2),                   -- overall runs index (context), 100 = avg
    source        TEXT NOT NULL,                   -- provenance, e.g. 'savant_3yr'
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (team_id, season, window_yrs, source)
);

CREATE INDEX IF NOT EXISTS idx_park_factors_team ON park_factors(team_id);

COMMENT ON TABLE  park_factors IS
  'Statcast park factors (Savant, 3-yr rolling). INDEX scale 100 = league average. v2 HR model input (Day 2). Distinct from legacy teams.park_hr_* multiplier columns.';
COMMENT ON COLUMN park_factors.team_id IS
  'FK to teams.id. Mapped from Savant main_team_id (== teams.mlb_id / MLB StatsAPI id) by pull_park_factors.py.';
COMMENT ON COLUMN park_factors.season IS
  'Season the rolling window is anchored to (e.g. 2026 → window 2024-2026).';
COMMENT ON COLUMN park_factors.window_yrs IS
  'Rolling window length in years. 3 = Savant default (savant_3yr); 1 reserved for single-season.';
COMMENT ON COLUMN park_factors.hr_factor IS
  'Overall HR park factor (Savant index_hr, batSide=all). 100 = league average; >100 favors HR.';
COMMENT ON COLUMN park_factors.hr_factor_lhb IS
  'HR park factor for left-handed batters (Savant index_hr, batSide=L). 100 = avg.';
COMMENT ON COLUMN park_factors.hr_factor_rhb IS
  'HR park factor for right-handed batters (Savant index_hr, batSide=R). 100 = avg.';
COMMENT ON COLUMN park_factors.runs_factor IS
  'Overall runs park factor (Savant index_runs, batSide=all). 100 = avg. Context only.';
COMMENT ON COLUMN park_factors.source IS
  'Provenance/window label, e.g. savant_3yr. Part of the upsert uniqueness key.';
