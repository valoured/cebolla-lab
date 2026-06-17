-- ════════════════════════════════════════════════════════════════════════
-- 36_picks_v2.sql  ·  v2 rebuild Day 5 — shadow HR picks (hr_score_v2)
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   picks_v2: shadow-only HR picks from the v2 formula model (hr_model.py),
--   parallel to cards/pods/projections. FULL SLATE — one row per confirmed-
--   lineup batter (option B; no team-cluster filter). NO staking columns —
--   tracking only, under stop-the-bleed.
--
--   picks_v2_rejected: forensics sink for picks that FAIL a calibration sanity
--   check (hard reject: model_prob_per_game > 0.30, or slate-level failure).
--   Rejected picks are NOT written to picks_v2.
--
-- MODEL
--   compute_hr_probability(): per_pa = MODEL_INTERCEPT_C(0.88) ×
--     shrunk_observed_hr_per_pa × batter_profile_factor × pitcher_factor ×
--     park_mult × weather_mult; per_game = 1-(1-per_pa)^E[PA]. Calibrated so
--     slate median edge ≈ 0 (validated 2026-06-17 harness, 270-batter slate).
--   edge_pct = per_game − no_vig (single-sided dynamic vig curve).
--
--   CREATE TABLE IF NOT EXISTS → IDEMPOTENT, safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS picks_v2 (
    id                   SERIAL PRIMARY KEY,
    pick_date            DATE NOT NULL,
    game_id              INTEGER REFERENCES games(id),
    player_id            INTEGER REFERENCES players(id),
    market               TEXT NOT NULL DEFAULT 'hr_anytime',
    model_version        TEXT NOT NULL,            -- 'hr_v2.0' (bump to v2.1 on weight retrain)
    model_prob_per_pa    NUMERIC(6, 5),
    model_prob_per_game  NUMERIC(6, 5),
    best_american_odds   INTEGER,
    no_vig_prob          NUMERIC(6, 5),
    edge_pct             NUMERIC(6, 4),
    edge_status          TEXT,                     -- strong_back…strong_fade | longshot_unrated
    components           JSONB,                    -- shrunk rate, each factor, feature z-scores, baselines
    warnings             JSONB,                    -- {is_fallback, data_age_days, lineup_source, longshot, per_game_high}
    lineup_source        TEXT,
    book_settled_outcome BOOLEAN,                  -- NULL until settled; TRUE=HR hit, FALSE=no HR
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (pick_date, game_id, player_id, market, model_version)
);

CREATE INDEX IF NOT EXISTS idx_picks_v2_date   ON picks_v2(pick_date);
CREATE INDEX IF NOT EXISTS idx_picks_v2_player ON picks_v2(player_id);

CREATE TABLE IF NOT EXISTS picks_v2_rejected (
    id                   SERIAL PRIMARY KEY,
    pick_date            DATE NOT NULL,
    game_id              INTEGER REFERENCES games(id),
    player_id            INTEGER REFERENCES players(id),
    market               TEXT NOT NULL DEFAULT 'hr_anytime',
    model_version        TEXT NOT NULL,
    reject_reason        TEXT,                     -- e.g. 'per_game_gt_0.30', 'slate_median_per_game'
    model_prob_per_game  NUMERIC(6, 5),
    components           JSONB,
    warnings             JSONB,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_picks_v2_rejected_date ON picks_v2_rejected(pick_date);

COMMENT ON TABLE  picks_v2 IS
  'Shadow-only v2 HR picks (hr_model.py, hr_v2.0). Full confirmed-lineup slate, tracking only (no stake). Distinct from cards/pods/projections.';
COMMENT ON COLUMN picks_v2.model_prob_per_game IS
  'Modeled P(1+ HR). per_pa = c(0.88) × shrunk_obs × batter × pitcher × park × weather; per_game = 1-(1-per_pa)^E[PA].';
COMMENT ON COLUMN picks_v2.edge_pct IS
  'model_prob_per_game − no_vig_prob (single-sided dynamic vig curve). NULL when odds missing or longshot-filtered.';
COMMENT ON COLUMN picks_v2.components IS
  'JSONB debug: shrunk_observed_hr_per_pa, batter_profile_factor, pitcher_factor, park_mult, weather_mult, feature_zs.';
COMMENT ON COLUMN picks_v2.warnings IS
  'JSONB flags: is_fallback (pitcher), data_age_days, lineup_source, longshot, per_game_high (>0.20).';
COMMENT ON COLUMN picks_v2.book_settled_outcome IS
  'Settlement result: TRUE if batter hit 1+ HR, FALSE if not, NULL until graded. For post-hoc calibration.';
COMMENT ON TABLE  picks_v2_rejected IS
  'Forensics sink for picks failing a calibration sanity check (hard reject per_game>0.30 or slate-level). NOT in picks_v2.';
