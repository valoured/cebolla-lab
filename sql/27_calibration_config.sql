-- ════════════════════════════════════════════════════════════════════════
-- 27_calibration_config.sql  ·  Patch 8 calibration thresholds (tunable)
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Seeds the daily calibration check's thresholds into model_thresholds so
--   calibration.py reads them via _cfg_num (everything tunable — no exceptions).
--   These are the knobs you'll most want to adjust once real settled-bet data
--   accumulates and the kill-switch sensitivity needs tuning.
--
--   calibration_min_sample is the safety guard: with fewer than this many
--   settled picks in the window, the check is SKIPPED (logs "insufficient
--   sample") — a single bad-variance day must never trip the v2→v1 kill switch.
--
-- IDEMPOTENT — ON CONFLICT DO NOTHING. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

INSERT INTO model_thresholds (key, num_value, text_value, market_class, unit, description) VALUES
  ('a_tier_hit_floor',          0.15, NULL, NULL, 'decimal',
     'Patch 8: high-severity alert if rolling A-tier hit rate < this.'),
  ('pod_hr_hit_floor',          0.12, NULL, NULL, 'decimal',
     'Patch 8: CRITICAL alert + auto-rollback if rolling POD HR hit rate < this.'),
  ('calibration_lookback_days', 14,   NULL, NULL, 'count',
     'Patch 8: rolling window (days) for calibration hit-rate computation.'),
  ('calibration_min_sample',    20,   NULL, NULL, 'count',
     'Patch 8: minimum settled picks per cohort to run a calibration check. Below this, log ''insufficient sample'' and skip (no alert, no rollback).')
ON CONFLICT (key) DO NOTHING;

-- ════════════════════════════════════════════════════════════════════════
-- END 27_calibration_config.sql
-- ════════════════════════════════════════════════════════════════════════
