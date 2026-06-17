-- ════════════════════════════════════════════════════════════════════════
-- 37_feature_baselines.sql  ·  v2 rebuild Day 5 — feature z-score baselines
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   feature_baselines: leaguewide mean + stdev per feature per window, used by
--   scripts/v2/feature_z_scores.py to z-score raw features in hr_model.py.
--   Populated by scripts/compute_feature_baselines.py (WEEKLY cron) from the
--   current batter_stats + pitcher_stats distributions.
--
--   Features seeded this pass:
--     pulled_airball_rate (batter, window 'season')   — batter profile factor
--     hr_per_9 / hr_per_fb / fb_pct (pitcher, 'l30')   — pitcher factor
--   Bootstrap values (2026-06-17 harness): pulled_airball ~17.3/5.7,
--   hr_per_9 ~1.21/0.81, hr_per_fb ~18.1/14.3, fb_pct ~26.8/8.5.
--
--   NOTE: `window` is a Postgres non-reserved keyword and is valid as a column
--   name here (PostgREST `.eq("window", ...)` works); kept per the locked spec.
--
--   CREATE TABLE IF NOT EXISTS → IDEMPOTENT, safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS feature_baselines (
    id          SERIAL PRIMARY KEY,
    feature     TEXT NOT NULL,            -- e.g. 'pulled_airball_rate', 'hr_per_9'
    window      TEXT NOT NULL,            -- 'season' | 'l30' | 'l14' | 'l7'
    mean        NUMERIC(10, 5),
    std         NUMERIC(10, 5),
    n           INTEGER,                  -- sample size the baseline was computed from
    computed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (feature, window)
);

CREATE INDEX IF NOT EXISTS idx_feature_baselines_feature ON feature_baselines(feature);

COMMENT ON TABLE  feature_baselines IS
  'Leaguewide mean/std per feature per window for z-score normalization (feature_z_scores.py / hr_model.py). Refreshed weekly by compute_feature_baselines.py.';
COMMENT ON COLUMN feature_baselines.window IS
  'Stat window the baseline is computed over: season/l30/l14/l7. Must match the window the consuming feature is read from.';
COMMENT ON COLUMN feature_baselines.n IS
  'Number of qualifying rows in the baseline sample (e.g. batters pa>=50, pitchers ip>=10 / bbe>=20).';
