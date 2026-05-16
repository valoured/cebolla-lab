-- ============================================================
-- CEBOLLA LABORATORY — Schema Migration 02
-- Adds lineups table for confirmed/projected batting orders
-- Run this in Supabase SQL Editor (additive, doesn't touch existing tables)
-- ============================================================

CREATE TABLE IF NOT EXISTS lineups (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER REFERENCES games(id) ON DELETE CASCADE,
    team_id         INTEGER REFERENCES teams(id),
    player_id       INTEGER REFERENCES players(id),
    batting_order   INTEGER,                       -- 1-9 (10 = DH spot in some leagues)
    position        TEXT,                          -- 'C', '1B', 'LF', 'DH', etc.
    bats            CHAR(1),                       -- 'L' | 'R' | 'S' (switch)
    is_confirmed    BOOLEAN DEFAULT FALSE,         -- True once MLB posts official lineup
    source          TEXT DEFAULT 'mlb_api',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(game_id, team_id, batting_order)
);

CREATE INDEX IF NOT EXISTS idx_lineups_game ON lineups(game_id);
CREATE INDEX IF NOT EXISTS idx_lineups_player ON lineups(player_id);

-- Helper view: today's full slate w/ batters keyed by game
CREATE OR REPLACE VIEW today_lineups AS
SELECT
    l.game_id,
    l.team_id,
    t.abbrev      AS team_abbrev,
    l.batting_order,
    l.position,
    l.bats,
    l.is_confirmed,
    p.id          AS player_id,
    p.name        AS player_name,
    p.mlbam_id
FROM lineups l
JOIN games g ON g.id = l.game_id
JOIN teams t ON t.id = l.team_id
JOIN players p ON p.id = l.player_id
WHERE g.game_date = CURRENT_DATE
ORDER BY l.game_id, l.team_id, l.batting_order;
