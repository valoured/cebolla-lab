-- ============================================================
-- CEBOLLA LABORATORY — Schema Migration 05
-- Phase 6: 1+ Hits market support
-- Adds BAA + hit-rate fields to pitcher_stats
-- Run in Supabase SQL Editor (additive, idempotent)
-- ============================================================

ALTER TABLE pitcher_stats
  ADD COLUMN IF NOT EXISTS hits_allowed   INTEGER,
  ADD COLUMN IF NOT EXISTS baa            NUMERIC(4, 3),    -- batting avg against
  ADD COLUMN IF NOT EXISTS hits_per_9     NUMERIC(4, 2),    -- hits allowed per 9 IP
  ADD COLUMN IF NOT EXISTS hit_per_pa     NUMERIC(5, 4);    -- hits / batters_faced
