-- ============================================================
-- CEBOLLA LABORATORY — Schema Migration 06
-- Live scores on the slate
-- Adds home_score, away_score, inning_state, current_inning to games
-- Run in Supabase SQL Editor (additive, idempotent)
-- ============================================================

ALTER TABLE games
  ADD COLUMN IF NOT EXISTS home_score      INTEGER,
  ADD COLUMN IF NOT EXISTS away_score      INTEGER,
  ADD COLUMN IF NOT EXISTS current_inning  INTEGER,
  ADD COLUMN IF NOT EXISTS inning_state    TEXT;       -- 'Top' | 'Bottom' | 'Middle' | 'End'
