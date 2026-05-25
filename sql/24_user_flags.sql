-- ════════════════════════════════════════════════════════════════════════
-- 24_user_flags.sql  ·  Patch 7 manual override flags + career_barrel_pct note
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   1. Creates `user_flags` — manual batter overrides the picker honors for a
--      given pick_date (Patch 7, applyUserFlags in pick_pod.py).
--   2. Updates the comment on batter_stats.career_barrel_pct to record that it
--      is the OVERALL career barrel%, with per-pitch-family data living in the
--      pa_vs_pitch JSONB (decision from the v2 reconciliation — Patch 4 reads
--      per-family barrel from pa_vs_pitch, not from this scalar).
--
-- HOW user_flags IS USED (Patch 7)
--   For each row matching the pick_date:
--     · If the framework already surfaced that batter → set a tier FLOOR of
--       'B+' (the pick can rank higher but never drop below B+).
--     · If the framework did NOT surface them → ADD them as a C+ lottery
--       candidate with stake_modifier = 0.4.
--   Conviction level adds to confidence_score:
--       gut +0.07 | matchup +0.10 | hot_streak +0.08
--
-- IDEMPOTENT — CREATE TABLE / INDEX use IF NOT EXISTS; COMMENT is harmless to
-- re-run. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS user_flags (
  id           SERIAL PRIMARY KEY,
  pick_date    DATE NOT NULL,
  batter_id    INTEGER NOT NULL REFERENCES players(id),
  conviction   TEXT NOT NULL CHECK (conviction IN ('gut','matchup','hot_streak')),
  market_class TEXT,                 -- 'hr' | 'hrr' | NULL (applies to any market)
  note         TEXT,                 -- free-text reason for the flag
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (pick_date, batter_id, market_class)
);

COMMENT ON TABLE user_flags IS
  'Patch 7: manual batter overrides the picker honors per pick_date. Surfaced picks get a B+ tier floor; unsurfaced flagged batters are ADDED as C+ lottery picks (stake_modifier 0.4). Conviction adds to confidence: gut +0.07 / matchup +0.10 / hot_streak +0.08.';
COMMENT ON COLUMN user_flags.conviction IS
  'Conviction level driving the confidence bonus: gut (+0.07), matchup (+0.10), hot_streak (+0.08).';
COMMENT ON COLUMN user_flags.market_class IS
  'Scope the flag to a market (''hr'' / ''hrr''), or NULL for any market.';

CREATE INDEX IF NOT EXISTS idx_user_flags_date ON user_flags(pick_date);

-- ──── career_barrel_pct comment update (v2 reconciliation, answer #2) ─────────
COMMENT ON COLUMN batter_stats.career_barrel_pct IS
  'Overall career barrel %, stored as PERCENT (0-100). Per-pitch-family career barrel data lives in pa_vs_pitch (Patch 4 reads per-family from there; this scalar is the overall fallback).';

-- ════════════════════════════════════════════════════════════════════════
-- END 24_user_flags.sql
-- ════════════════════════════════════════════════════════════════════════
