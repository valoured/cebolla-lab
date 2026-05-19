-- ────────────────────────────────────────────────────────────────
-- Migration 15: Batter counting stats (runs + RBIs)
--
-- Adds runs and RBI columns to batter_stats so the H+R+RBI (HRR) market
-- projection model can compute per-PA run-scoring and RBI rates.
--
-- These stats come from MLB Stats API (pull_batter_counting_stats.py),
-- NOT from Savant statcast events. Statcast tracks per-PA outcomes but
-- doesn't carry runs/RBI per PA as direct fields — we'd have to
-- reconstruct from base-state changes, which is fragile. MLB Stats API
-- has these as authoritative aggregates.
--
-- Safe to run on a populated table. Existing rows get NULLs for the new
-- columns until pull_batter_counting_stats.py next runs and backfills.
-- ────────────────────────────────────────────────────────────────

ALTER TABLE batter_stats
  ADD COLUMN IF NOT EXISTS runs       INTEGER,
  ADD COLUMN IF NOT EXISTS rbis       INTEGER,
  ADD COLUMN IF NOT EXISTS r_per_pa   NUMERIC(5, 4),
  ADD COLUMN IF NOT EXISTS rbi_per_pa NUMERIC(5, 4);

-- Documentation
COMMENT ON COLUMN batter_stats.runs IS
  'Total runs scored by the batter in this window (from MLB Stats API).';
COMMENT ON COLUMN batter_stats.rbis IS
  'Total RBIs by the batter in this window (from MLB Stats API).';
COMMENT ON COLUMN batter_stats.r_per_pa IS
  'Runs per plate appearance, used in H+R+RBI Poisson projection.';
COMMENT ON COLUMN batter_stats.rbi_per_pa IS
  'RBIs per plate appearance, used in H+R+RBI Poisson projection.';
