-- 31_lineup_source.sql
-- ────────────────────────────────────────────────────────────────────────
-- Lineup provenance on projections (Option B rolling-7 predicted lineups).
--
-- Records whether each projection was built on a CONFIRMED MLB lineup or an
-- ESTIMATED one, and which estimator produced it. Critical for the tier_v1
-- stake calibration re-audit: a Lock POD staked at 2U built on a *guessed*
-- lineup must be distinguishable from one built on a confirmed lineup.
--
--   confirmed             — real MLB-posted lineup (source='mlb_api', complete 1-9)
--   estimated_rolling_7   — Option B: rolling-7 most-common + handedness layer
--   estimated_last_known  — degraded fallback (single most-recent; <7 games history)
--
-- Forward-only, safe to re-run. Legacy rows stay NULL — calibration queries
-- filter `lineup_source = 'confirmed'` (or IS NOT NULL) to compare like-for-like.
-- Pickers do NOT read this column yet (record-only this phase).
-- ────────────────────────────────────────────────────────────────────────

ALTER TABLE projections
  ADD COLUMN IF NOT EXISTS lineup_source TEXT
    CHECK (lineup_source IS NULL OR lineup_source IN
           ('confirmed', 'estimated_rolling_7', 'estimated_last_known'));

COMMENT ON COLUMN projections.lineup_source IS
  'Lineup provenance behind this projection. NULL = legacy (pre-Phase2). '
  'confirmed = MLB-posted complete lineup; estimated_rolling_7 = Option B '
  'rolling-7 most-common (handedness-layered); estimated_last_known = single '
  'most-recent fallback (thin history). Recorded only — pickers do not filter on it yet.';

-- ── Reversal (manual) ─────────────────────────────────────────────────────
-- ALTER TABLE projections DROP COLUMN IF EXISTS lineup_source;
