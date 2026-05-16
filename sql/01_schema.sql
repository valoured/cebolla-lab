-- ============================================================
-- CEBOLLA LABORATORY — Database Schema v1
-- Run this in Supabase SQL Editor after creating a new project
-- ============================================================

-- Drop if you're re-running; safe on a fresh project
DROP TABLE IF EXISTS bet_log CASCADE;
DROP TABLE IF EXISTS projections CASCADE;
DROP TABLE IF EXISTS odds_snapshots CASCADE;
DROP TABLE IF EXISTS bvp_history CASCADE;
DROP TABLE IF EXISTS batter_stats CASCADE;
DROP TABLE IF EXISTS pitcher_arsenals CASCADE;
DROP TABLE IF EXISTS games CASCADE;
DROP TABLE IF EXISTS players CASCADE;
DROP TABLE IF EXISTS teams CASCADE;

-- ----------------------------------------------------------------
-- TEAMS (30 rows, mostly static reference data)
-- ----------------------------------------------------------------
CREATE TABLE teams (
    id              SERIAL PRIMARY KEY,
    mlb_id          INTEGER UNIQUE NOT NULL,         -- MLB API team ID
    abbrev          TEXT UNIQUE NOT NULL,            -- e.g. 'ARI', 'COL'
    name            TEXT NOT NULL,
    league          TEXT,                            -- 'AL' / 'NL'
    division        TEXT,
    stadium         TEXT,
    stadium_lat     NUMERIC(8, 5),                   -- For Open-Meteo
    stadium_lng     NUMERIC(8, 5),
    park_hr_factor  NUMERIC(4, 2) DEFAULT 1.00,      -- Overall HR factor
    park_hr_lhb     NUMERIC(4, 2) DEFAULT 1.00,      -- LHB HR factor
    park_hr_rhb     NUMERIC(4, 2) DEFAULT 1.00,      -- RHB HR factor
    park_ba_factor  NUMERIC(4, 2) DEFAULT 1.00,      -- Hits factor
    is_dome         BOOLEAN DEFAULT FALSE,           -- Skip weather for domes
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ----------------------------------------------------------------
-- PLAYERS (batters + pitchers, identified by MLBAM ID)
-- ----------------------------------------------------------------
CREATE TABLE players (
    id              SERIAL PRIMARY KEY,
    mlbam_id        INTEGER UNIQUE NOT NULL,         -- pybaseball key_mlbam
    fg_id           INTEGER,                         -- FanGraphs id (optional)
    name            TEXT NOT NULL,
    team_id         INTEGER REFERENCES teams(id),
    position        TEXT,                            -- 'P', 'C', '1B', etc.
    bats            CHAR(1),                         -- 'L', 'R', 'S'
    throws          CHAR(1),                         -- 'L', 'R'
    is_pitcher      BOOLEAN DEFAULT FALSE,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_players_team ON players(team_id);
CREATE INDEX idx_players_pitcher ON players(is_pitcher) WHERE is_pitcher = TRUE;

-- ----------------------------------------------------------------
-- GAMES (one row per scheduled MLB game)
-- ----------------------------------------------------------------
CREATE TABLE games (
    id                  SERIAL PRIMARY KEY,
    mlb_game_pk         INTEGER UNIQUE NOT NULL,     -- MLB API gamePk
    game_date           DATE NOT NULL,
    game_time_utc       TIMESTAMPTZ,
    away_team_id        INTEGER REFERENCES teams(id),
    home_team_id        INTEGER REFERENCES teams(id),
    away_pitcher_id     INTEGER REFERENCES players(id),
    home_pitcher_id     INTEGER REFERENCES players(id),
    venue               TEXT,
    status              TEXT,                        -- 'Scheduled', 'In Progress', 'Final'
    -- Weather snapshot (last update before first pitch)
    temp_f              NUMERIC(4, 1),
    wind_mph            NUMERIC(4, 1),
    wind_dir_deg        INTEGER,                     -- 0-360
    wind_label          TEXT,                        -- 'out to CF', 'in from LF', etc.
    precip_pct          INTEGER,
    weather_updated_at  TIMESTAMPTZ,
    -- Aggregate HR factor (park × weather × handedness)
    hr_factor_lhb       NUMERIC(5, 3),
    hr_factor_rhb       NUMERIC(5, 3),
    hr_factor_overall   NUMERIC(5, 3),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_games_date ON games(game_date);
CREATE INDEX idx_games_pitchers ON games(away_pitcher_id, home_pitcher_id);

-- ----------------------------------------------------------------
-- PITCHER ARSENALS (per pitcher, per pitch type, per batter side)
-- This is the heart of the "Krash rating" replica
-- ----------------------------------------------------------------
CREATE TABLE pitcher_arsenals (
    id              SERIAL PRIMARY KEY,
    pitcher_id      INTEGER REFERENCES players(id),
    season          INTEGER NOT NULL,
    window_type     TEXT NOT NULL,                   -- 'season' | 'l15g' | 'l50g' | 'l100g'
    vs_stance       CHAR(1) NOT NULL,                -- 'L' | 'R'
    pitch_type      TEXT NOT NULL,                   -- '4SM', 'CH', 'CT', 'SL', 'SI', etc.
    usage_pct       NUMERIC(5, 2),                   -- 0-100
    velo_avg        NUMERIC(4, 1),
    pitches         INTEGER,
    pa              INTEGER,
    hr              INTEGER,
    hr_pct          NUMERIC(5, 2),
    barrel_pct      NUMERIC(5, 2),
    hard_hit_pct    NUMERIC(5, 2),
    ev_avg          NUMERIC(5, 1),
    la_avg          NUMERIC(4, 1),
    whiff_pct       NUMERIC(5, 2),
    -- Composite grade (the "Krash" equivalent, lower = pitcher gets hit hard on this pitch)
    krash_rating    INTEGER,                         -- 0-100
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(pitcher_id, season, window_type, vs_stance, pitch_type)
);

CREATE INDEX idx_arsenals_pitcher ON pitcher_arsenals(pitcher_id);

-- ----------------------------------------------------------------
-- BATTER STATS (rolling window stats per batter)
-- ----------------------------------------------------------------
CREATE TABLE batter_stats (
    id              SERIAL PRIMARY KEY,
    batter_id       INTEGER REFERENCES players(id),
    season          INTEGER NOT NULL,
    window_type     TEXT NOT NULL,                   -- 'season' | 'l15g' | 'l50g' | 'l100g'
    vs_hand         CHAR(1),                         -- 'L' | 'R' | 'A' (all)
    pa              INTEGER,
    ab              INTEGER,
    hits            INTEGER,
    hr              INTEGER,
    avg             NUMERIC(4, 3),
    obp             NUMERIC(4, 3),
    slg             NUMERIC(4, 3),
    iso             NUMERIC(4, 3),
    hr_per_pa       NUMERIC(5, 4),
    hit_per_pa      NUMERIC(5, 4),
    barrel_pct      NUMERIC(5, 2),
    hard_hit_pct    NUMERIC(5, 2),
    ev_avg          NUMERIC(5, 1),
    la_avg          NUMERIC(4, 1),
    pull_pct        NUMERIC(5, 2),
    -- Per-pitch-type performance (used by arsenal matchup adjustment)
    -- Stored as JSONB: {"4SM": {"hr_pct": 5.2, "ba": .278, ...}, "CH": {...}}
    by_pitch_type   JSONB,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(batter_id, season, window_type, vs_hand)
);

CREATE INDEX idx_batter_stats_player ON batter_stats(batter_id);

-- ----------------------------------------------------------------
-- BVP HISTORY (career batter vs pitcher)
-- ----------------------------------------------------------------
CREATE TABLE bvp_history (
    id              SERIAL PRIMARY KEY,
    batter_id       INTEGER REFERENCES players(id),
    pitcher_id      INTEGER REFERENCES players(id),
    pa              INTEGER,
    ab              INTEGER,
    hits            INTEGER,
    hr              INTEGER,
    bb              INTEGER,
    so              INTEGER,
    avg             NUMERIC(4, 3),
    ops             NUMERIC(4, 3),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(batter_id, pitcher_id)
);

CREATE INDEX idx_bvp_lookup ON bvp_history(pitcher_id, batter_id);

-- ----------------------------------------------------------------
-- ODDS SNAPSHOTS (timestamped odds for props)
-- ----------------------------------------------------------------
CREATE TABLE odds_snapshots (
    id              SERIAL PRIMARY KEY,
    game_id         INTEGER REFERENCES games(id),
    player_id       INTEGER REFERENCES players(id),
    market          TEXT NOT NULL,                   -- 'hr_0.5_over', 'hits_0.5_over', etc.
    book            TEXT NOT NULL,                   -- 'draftkings', 'fanduel', etc.
    american_odds   INTEGER,
    decimal_odds    NUMERIC(6, 3),
    implied_prob    NUMERIC(6, 4),                   -- raw with vig
    line            NUMERIC(4, 1),                   -- e.g. 0.5, 1.5
    snapshot_time   TIMESTAMPTZ DEFAULT NOW(),
    is_current      BOOLEAN DEFAULT TRUE             -- only one TRUE per game/player/market/book
);

CREATE INDEX idx_odds_current ON odds_snapshots(game_id, player_id, market, book) WHERE is_current = TRUE;
CREATE INDEX idx_odds_history ON odds_snapshots(player_id, market, snapshot_time DESC);

-- ----------------------------------------------------------------
-- PROJECTIONS (model output per player per game per market)
-- ----------------------------------------------------------------
CREATE TABLE projections (
    id                  SERIAL PRIMARY KEY,
    game_id             INTEGER REFERENCES games(id),
    player_id           INTEGER REFERENCES players(id),
    market              TEXT NOT NULL,               -- 'hr_0.5', 'hits_0.5'
    model_version       TEXT NOT NULL,               -- 'v0.1', 'v0.2', etc.
    projected_prob      NUMERIC(6, 4),               -- our model's probability
    -- Components for transparency
    base_rate           NUMERIC(6, 4),
    pitcher_adj         NUMERIC(5, 3),
    park_adj            NUMERIC(5, 3),
    weather_adj         NUMERIC(5, 3),
    arsenal_adj         NUMERIC(5, 3),
    -- Edge vs current best odds (devigged)
    best_book           TEXT,
    best_american_odds  INTEGER,
    no_vig_prob         NUMERIC(6, 4),
    edge                NUMERIC(6, 4),               -- projected_prob - no_vig_prob
    edge_bucket         TEXT,                        -- 'strong_fade', 'lean_fade', 'flat', 'lean_back', 'strong_back'
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(game_id, player_id, market, model_version)
);

CREATE INDEX idx_proj_edge ON projections(edge DESC);
CREATE INDEX idx_proj_game ON projections(game_id);

-- ----------------------------------------------------------------
-- BET LOG (your bets, the KrashBoard-killer feature)
-- ----------------------------------------------------------------
CREATE TABLE bet_log (
    id                  SERIAL PRIMARY KEY,
    placed_at           TIMESTAMPTZ DEFAULT NOW(),
    game_id             INTEGER REFERENCES games(id),
    player_id           INTEGER REFERENCES players(id),
    market              TEXT NOT NULL,
    side                TEXT,                        -- 'over', 'under', 'yes', 'no'
    line                NUMERIC(4, 1),
    book                TEXT,
    american_odds       INTEGER,
    stake               NUMERIC(8, 2),
    -- Snapshot at time of bet (for backtesting)
    edge_at_placement   NUMERIC(6, 4),
    projected_prob      NUMERIC(6, 4),
    model_version       TEXT,
    -- Result
    result              TEXT,                        -- 'pending', 'win', 'loss', 'push', 'void'
    payout              NUMERIC(8, 2),
    pnl                 NUMERIC(8, 2),
    notes               TEXT
);

CREATE INDEX idx_bet_log_date ON bet_log(placed_at DESC);
CREATE INDEX idx_bet_log_result ON bet_log(result);

-- ----------------------------------------------------------------
-- HELPER VIEW: today's slate with everything you need
-- ----------------------------------------------------------------
CREATE OR REPLACE VIEW today_slate AS
SELECT
    g.id              AS game_id,
    g.game_date,
    g.game_time_utc,
    at.abbrev         AS away_abbrev,
    ht.abbrev         AS home_abbrev,
    ht.stadium,
    g.temp_f,
    g.wind_mph,
    g.wind_label,
    g.precip_pct,
    g.hr_factor_overall,
    g.hr_factor_lhb,
    g.hr_factor_rhb,
    ap.name           AS away_pitcher,
    hp.name           AS home_pitcher,
    g.status
FROM games g
JOIN teams at  ON at.id = g.away_team_id
JOIN teams ht  ON ht.id = g.home_team_id
LEFT JOIN players ap ON ap.id = g.away_pitcher_id
LEFT JOIN players hp ON hp.id = g.home_pitcher_id
WHERE g.game_date = CURRENT_DATE
ORDER BY g.game_time_utc;

-- ----------------------------------------------------------------
-- HELPER VIEW: ROI tracker by edge bucket
-- ----------------------------------------------------------------
CREATE OR REPLACE VIEW roi_by_edge_bucket AS
SELECT
    CASE
        WHEN edge_at_placement >= 0.05 THEN '5%+ edge'
        WHEN edge_at_placement >= 0.03 THEN '3-5% edge'
        WHEN edge_at_placement >= 0.01 THEN '1-3% edge'
        WHEN edge_at_placement >= 0    THEN '0-1% edge'
        ELSE 'negative edge'
    END AS bucket,
    COUNT(*)                                                AS bets,
    SUM(stake)                                              AS total_staked,
    SUM(COALESCE(pnl, 0))                                   AS total_pnl,
    ROUND(SUM(COALESCE(pnl, 0)) / NULLIF(SUM(stake), 0), 4) AS roi
FROM bet_log
WHERE result IN ('win', 'loss', 'push')
GROUP BY 1
ORDER BY MIN(edge_at_placement) DESC;

-- ============================================================
-- DONE. Next: run scripts/seed_teams.py to populate teams.
-- ============================================================
