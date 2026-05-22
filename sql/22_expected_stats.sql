-- ════════════════════════════════════════════════════════════════════════
-- 22_expected_stats.sql  ·  Ensure xSLG / xBA / xwOBA columns exist
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   The tier-system math needs xSLG L14 (HR Tier 1) and xBA L14 (HRR Tier 1).
--   pull_savant.py has been writing these columns for months, but no migration
--   file in this repo declares them — they were added directly to production
--   at some point. This migration retroactively documents them.
--
--   If the columns already exist (which they should), this is a no-op.
--   If a fresh DB is being initialized, this ensures they're present.
--
-- IDEMPOTENT — safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE batter_stats
  ADD COLUMN IF NOT EXISTS xba    NUMERIC(4, 3),    -- expected batting average from Savant
  ADD COLUMN IF NOT EXISTS xslg   NUMERIC(4, 3),    -- expected slugging
  ADD COLUMN IF NOT EXISTS xwoba  NUMERIC(4, 3);    -- expected wOBA

COMMENT ON COLUMN batter_stats.xba IS
  'Expected batting average from Statcast. Used in tier system HRR Tier 1 (threshold ≥ .280 L14).';
COMMENT ON COLUMN batter_stats.xslg IS
  'Expected slugging from Statcast. Used in tier system HR Tier 1 (threshold ≥ .600 L14) and contact_score composite.';
COMMENT ON COLUMN batter_stats.xwoba IS
  'Expected wOBA from Statcast. Not currently used in pickers, kept for future.';
