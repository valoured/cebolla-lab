-- ════════════════════════════════════════════════════════════════════════
-- 32_pulled_airball.sql  ·  v2 rebuild Day 1 — pulled-airball HR predictor
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Adds two per-window batted-ball-direction rates to batter_stats:
--     pulled_fb_rate       — pulled fly balls            as % of BBE
--     pulled_airball_rate  — pulled (fly + line drives)  as % of BBE
--
--   Pulled airballs are the #1 public HR predictor: pulled fly balls produce
--   ~37.3% HR vs ~4.6% opposite field. Popups are EXCLUDED from "airball"
--   (≈0% HR). Computed in pull_savant.aggregate_batter alongside pull_pct /
--   barrel_pct for all four windows (season / L30 / L14 / L7).
--
--   "Pulled" uses the SPRAY-ANGLE pull-THIRD definition (Bill Petti
--   convention, 15° threshold, sign-flipped by handedness) — distinct from
--   the existing pull_pct column, which is a half-field split kept unchanged
--   as a separate, coarser signal. Both rates are denominated over BBE so
--   they share scale with barrel_pct / pull_pct.
--
--   If the columns already exist, this is a no-op. IDEMPOTENT — safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

ALTER TABLE batter_stats
  ADD COLUMN IF NOT EXISTS pulled_fb_rate      NUMERIC(5, 2),  -- pulled fly balls / BBE (%)
  ADD COLUMN IF NOT EXISTS pulled_airball_rate NUMERIC(5, 2);  -- pulled fly+line / BBE (%)

COMMENT ON COLUMN batter_stats.pulled_fb_rate IS
  'Pulled fly balls as % of batted-ball events. Pull = spray-angle pull-third (Bill Petti, 15° threshold). v2 HR model input.';
COMMENT ON COLUMN batter_stats.pulled_airball_rate IS
  'Pulled airballs (fly_ball + line_drive, popups excluded) as % of BBE. #1 public HR predictor (~37% HR pulled flies vs ~5% oppo). v2 HR model input.';
