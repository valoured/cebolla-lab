-- ════════════════════════════════════════════════════════════════════════
-- 26_league_constants.sql  ·  Patch 4 league_hr_per_barrel (tunable)
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Seeds league_hr_per_barrel into model_thresholds. Patch 4
--   (calculate_primary_signal) reads it via _cfg_required — there is NO code
--   fallback. If this row is missing AND model_thresholds is unreachable,
--   pick_pod aborts (it cannot compute the Patch 4 base prediction), by design:
--   we never silently substitute a guessed conversion rate.
--
--   NOTE: the pitcher-factor constants (hr_per_9 / 1.15, clamp [0.75, 1.40])
--   are intentionally NOT seeded — they are internal invariants mirrored from
--   compute_projections.py (single source of truth is the projection model;
--   seeding them separately would risk pick_pod drifting from the projections
--   it scores). See the module constants + docstring in pick_pod.py.
--
-- IDEMPOTENT — ON CONFLICT DO NOTHING. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

INSERT INTO model_thresholds (key, num_value, market_class, unit, description) VALUES
  ('league_hr_per_barrel', 0.45, 'hr', 'decimal',
   'Patch 4: league-average HR-per-barrel conversion rate. Tunable as Statcast data evolves or ball-construction changes affect the rate. REQUIRED (no code fallback) — pick_pod aborts if absent and config is unreachable.')
ON CONFLICT (key) DO NOTHING;

-- ════════════════════════════════════════════════════════════════════════
-- END 26_league_constants.sql
-- ════════════════════════════════════════════════════════════════════════
