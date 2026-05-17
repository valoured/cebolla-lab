-- ============================================================
-- CEBOLLA LABORATORY — Schema Migration 03
-- Adds pitcher_stats table for clean season-level pitching stats
-- Run this in Supabase SQL Editor (additive)
-- ============================================================

CREATE TABLE IF NOT EXISTS pitcher_stats (
    id              SERIAL PRIMARY KEY,
    pitcher_id      INTEGER REFERENCES players(id) ON DELETE CASCADE,
    season          INTEGER NOT NULL,
    window_type     TEXT NOT NULL,                   -- 'season' for now
    -- Volume
    games_started   INTEGER,
    innings_pitched NUMERIC(6, 1),
    batters_faced   INTEGER,
    -- Outcomes
    hr_allowed      INTEGER,
    bb              INTEGER,
    so              INTEGER,
    er              INTEGER,
    -- Rates
    era             NUMERIC(4, 2),
    fip             NUMERIC(4, 2),
    whip            NUMERIC(4, 2),
    hr_per_9        NUMERIC(4, 2),
    k_per_9         NUMERIC(4, 2),
    bb_per_9        NUMERIC(4, 2),
    hr_per_pa       NUMERIC(5, 4),
    -- Throws / handedness (also in players, denormalized for convenience)
    throws          CHAR(1),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(pitcher_id, season, window_type)
);

CREATE INDEX IF NOT EXISTS idx_pitcher_stats_pitcher ON pitcher_stats(pitcher_id);
CREATE INDEX IF NOT EXISTS idx_pitcher_stats_season ON pitcher_stats(season);
