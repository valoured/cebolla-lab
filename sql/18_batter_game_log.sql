-- ────────────────────────────────────────────────────────────────
-- Migration 18: Batter game log — one row per batter per MLB game.
--
-- Why this exists:
--   The batter_stats table holds aggregate windows (season, l14, l50,
--   l100) with no per-game granularity. That's fine for projections
--   but it can't answer questions like:
--
--     "Has this batter recorded a hit in 9 of his last 10 games?"
--     "What's his H+R+RBI o1.5 hit rate over the last 5 starts?"
--     "Is he on a 10-game H/R/RBI streak?"
--
--   Streaks-style UIs (KrashBoard's L5/L10/L15/L20/SZN ring charts)
--   need per-game rows so the front-end can rebuild any window the
--   user clicks. This table is the source-of-truth for that.
--
-- What it stores:
--   One row per (batter, game). Has the basic batting line plus the
--   handful of derived flags we care about for streak markets:
--     - had_hit         : at least one hit in the game (Hits o0.5)
--     - had_hr          : at least one HR (HR Anytime)
--     - total_bases     : 1B + 2*2B + 3*3B + 4*HR (Total Bases markets)
--     - h_r_rbi         : hits + runs + rbis     (H+R+RBI markets)
--
-- Source:
--   pull_batter_game_log.py walks MLB Stats API per-game boxscores.
--   The Stats API is the authoritative source for batting lines (more
--   reliable than reconstructing from Statcast pitch events) and
--   matches what sportsbook lines settle against.
--
-- Idempotency:
--   Upsert keyed on (batter_id, game_id). Re-running the puller is
--   safe — values get overwritten with the latest boxscore numbers.
--   The pull script is responsible for only updating final games.
--
-- Safe to run on a populated DB. Existing tables untouched.
-- ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS batter_game_log (
    id              SERIAL PRIMARY KEY,
    batter_id       INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_id         INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    game_date       DATE NOT NULL,                  -- denorm from games for fast date-range queries
    team_id         INTEGER REFERENCES teams(id),
    opponent_team_id INTEGER REFERENCES teams(id),
    is_home         BOOLEAN,
    batting_order   INTEGER,                        -- 1-9, NULL if pinch-hit
    bats            CHAR(1),                        -- 'L' | 'R' | 'S' (at the time of the game)
    vs_hand         CHAR(1),                        -- handedness of opposing starting pitcher

    -- Counting stats
    pa              INTEGER,
    ab              INTEGER,
    hits            INTEGER,
    doubles         INTEGER,
    triples         INTEGER,
    hr              INTEGER,
    rbis            INTEGER,
    runs            INTEGER,
    bb              INTEGER,
    so              INTEGER,                        -- strikeouts
    hbp             INTEGER,
    sb              INTEGER,                        -- stolen bases

    -- Derived flags (denormalized to keep streak queries trivial)
    had_hit         BOOLEAN,                        -- hits >= 1 (Hits o0.5)
    had_hr          BOOLEAN,                        -- hr >= 1 (HR Anytime)
    had_rbi         BOOLEAN,                        -- rbis >= 1
    had_run         BOOLEAN,                        -- runs >= 1
    total_bases     INTEGER,                        -- 1B + 2*2B + 3*3B + 4*HR
    h_r_rbi         INTEGER,                        -- hits + runs + rbis

    -- Provenance / status
    game_status     TEXT,                           -- snapshot at pull time
    source          TEXT DEFAULT 'mlb_api',
    pulled_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE(batter_id, game_id)
);

-- Indexes for the queries the frontend will run:
--   1) per-batter recent games (Streaks page L5/L10/L15/L20)
--   2) per-date all batters (date-bounded scans for slate)
--   3) per-game (rarely; for game settle / debug)
CREATE INDEX IF NOT EXISTS idx_bgl_batter_date
    ON batter_game_log(batter_id, game_date DESC);
CREATE INDEX IF NOT EXISTS idx_bgl_date
    ON batter_game_log(game_date DESC);
CREATE INDEX IF NOT EXISTS idx_bgl_game
    ON batter_game_log(game_id);

-- ────────────────────────────────────────────────────────────────
-- View: batter_recent_form
--
-- Pre-computed L5/L10/L15/L20/SZN aggregates per batter. The frontend
-- can hit this view directly to render the Krashboard-style ring
-- charts without any extra math in JS.
--
-- IMPORTANT — this is a regular VIEW (not materialized). Reads scan
-- batter_game_log at query time. With the (batter_id, game_date DESC)
-- index this is fast enough at our scale (~1000 batters × ~150
-- games/season = 150k rows tops). If it ever gets slow, swap to a
-- materialized view refreshed nightly by a cron job.
-- ────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW batter_recent_form AS
WITH ranked AS (
    SELECT
        batter_id,
        game_date,
        had_hit,
        had_hr,
        had_rbi,
        had_run,
        hits,
        hr,
        rbis,
        runs,
        total_bases,
        h_r_rbi,
        pa,
        ROW_NUMBER() OVER (
            PARTITION BY batter_id
            ORDER BY game_date DESC, game_id DESC
        ) AS recency_rank
    FROM batter_game_log
    WHERE pa > 0      -- exclude DNPs / didn't bat
)
SELECT
    batter_id,
    COUNT(*)                                          AS games_szn,

    -- L5 window
    COUNT(*) FILTER (WHERE recency_rank <= 5)              AS games_l5,
    COUNT(*) FILTER (WHERE recency_rank <= 5 AND had_hit)  AS hits_g_l5,
    COUNT(*) FILTER (WHERE recency_rank <= 5 AND had_hr)   AS hr_g_l5,

    -- L10
    COUNT(*) FILTER (WHERE recency_rank <= 10)             AS games_l10,
    COUNT(*) FILTER (WHERE recency_rank <= 10 AND had_hit) AS hits_g_l10,
    COUNT(*) FILTER (WHERE recency_rank <= 10 AND had_hr)  AS hr_g_l10,
    SUM(hits)  FILTER (WHERE recency_rank <= 10)           AS hits_t_l10,
    SUM(hr)    FILTER (WHERE recency_rank <= 10)           AS hr_t_l10,
    SUM(total_bases) FILTER (WHERE recency_rank <= 10)     AS tb_t_l10,
    SUM(h_r_rbi) FILTER (WHERE recency_rank <= 10)         AS hrr_t_l10,

    -- L15
    COUNT(*) FILTER (WHERE recency_rank <= 15)             AS games_l15,
    COUNT(*) FILTER (WHERE recency_rank <= 15 AND had_hit) AS hits_g_l15,
    COUNT(*) FILTER (WHERE recency_rank <= 15 AND had_hr)  AS hr_g_l15,

    -- L20
    COUNT(*) FILTER (WHERE recency_rank <= 20)             AS games_l20,
    COUNT(*) FILTER (WHERE recency_rank <= 20 AND had_hit) AS hits_g_l20,
    COUNT(*) FILTER (WHERE recency_rank <= 20 AND had_hr)  AS hr_g_l20,

    -- Season totals
    COUNT(*) FILTER (WHERE had_hit)                        AS hits_g_szn,
    COUNT(*) FILTER (WHERE had_hr)                         AS hr_g_szn
FROM ranked
GROUP BY batter_id;

COMMENT ON VIEW batter_recent_form IS
    'Per-batter L5/L10/L15/L20/SZN game-flag aggregates. Powers the '
    'Streaks page ring charts. Built from batter_game_log; updates '
    'whenever the table updates.';

-- ────────────────────────────────────────────────────────────────
-- Helper view: active streaks for "had hit" and "had HR".
--
-- An "active streak" = N most recent consecutive games where the flag
-- is TRUE, starting from the most recent game. As soon as the flag is
-- FALSE, the streak is broken.
--
-- Postgres pattern: window over recency-ranked games; the streak is
-- the count of games before the first FALSE.
-- ────────────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW batter_active_streaks AS
WITH ranked AS (
    SELECT
        batter_id,
        game_date,
        had_hit,
        had_hr,
        had_rbi,
        ROW_NUMBER() OVER (
            PARTITION BY batter_id
            ORDER BY game_date DESC, game_id DESC
        ) AS recency_rank
    FROM batter_game_log
    WHERE pa > 0
),
-- For each batter, find the first rank where the flag is FALSE.
-- Streak length = (first_false_rank - 1). If no false ever, streak =
-- max recency_rank (i.e. all season).
first_break_hit AS (
    SELECT batter_id, MIN(recency_rank) AS first_break_rank
    FROM ranked
    WHERE NOT had_hit
    GROUP BY batter_id
),
first_break_hr AS (
    SELECT batter_id, MIN(recency_rank) AS first_break_rank
    FROM ranked
    WHERE NOT had_hr
    GROUP BY batter_id
),
last_games AS (
    SELECT batter_id, MAX(recency_rank) AS total_games
    FROM ranked
    GROUP BY batter_id
)
SELECT
    lg.batter_id,
    COALESCE(fbh.first_break_rank, lg.total_games + 1) - 1 AS hit_streak,
    COALESCE(fbhr.first_break_rank, lg.total_games + 1) - 1 AS hr_streak,
    lg.total_games AS games_played
FROM last_games lg
LEFT JOIN first_break_hit fbh ON fbh.batter_id = lg.batter_id
LEFT JOIN first_break_hr fbhr ON fbhr.batter_id = lg.batter_id;

COMMENT ON VIEW batter_active_streaks IS
    'Per-batter current active streaks for had_hit and had_hr. '
    'Streak = consecutive games starting from the most recent.';
