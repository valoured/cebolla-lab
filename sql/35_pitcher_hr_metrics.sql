-- ════════════════════════════════════════════════════════════════════════
-- 35_pitcher_hr_metrics.sql  ·  v2 rebuild Day 4 — pitcher fly-ball HR metrics
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Adds two Statcast-derived fly-ball metrics to pitcher_stats:
--     fb_pct    — fly balls as % of batted-ball events     (FanGraphs: FB / BBE)
--     hr_per_fb — HR as % of fly balls allowed             (FanGraphs: HR / FB)
--   Computed by pull_savant.aggregate_pitcher() from raw Statcast (bb_type /
--   events) for ALL windows it already writes (season / l30 / l14 / l7).
--
--   The pitcher half of the v2 HR matchup (homer-proneness in recent form):
--   a fly-ball pitcher (high fb_pct) with a high hr_per_fb facing pull hitters
--   is the target profile.
--
-- ALREADY EXIST (migration 03) — NOT re-added:
--   hr_per_9 NUMERIC(4,2), innings_pitched NUMERIC(6,1). For Day 4 these get an
--   L30 row populated via the MLB Stats API byDateRange pull in
--   pull_pitcher_stats.py (clean IP — NOT estimated from Statcast, per Lesson 3).
--   Windowed HR/9 is L30-only this pass; fb_pct/hr_per_fb cover all 4 windows.
--
--   ADD COLUMN IF NOT EXISTS → IDEMPOTENT, safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE pitcher_stats
  ADD COLUMN IF NOT EXISTS fb_pct    NUMERIC(5, 2),   -- fly balls / BBE (%)
  ADD COLUMN IF NOT EXISTS hr_per_fb NUMERIC(5, 2);   -- HR / fly balls (%)

COMMENT ON COLUMN pitcher_stats.fb_pct IS
  'Fly-ball rate allowed: fly_ball / all batted-ball events (%), from Statcast bb_type. FanGraphs FB% convention. All windows (season/l30/l14/l7). v2 HR model input.';
COMMENT ON COLUMN pitcher_stats.hr_per_fb IS
  'HR per fly ball allowed: home runs / fly balls (%), from Statcast events/bb_type. FanGraphs HR/FB convention. All windows. v2 HR model input.';
