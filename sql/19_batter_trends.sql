-- ─────────────────────────────────────────────────────────────────
-- Migration 19: batter_trends
--
-- Daily snapshot of multi-signal trend scores per batter. One row per
-- (batter_id, trend_date) — overwritten each day by compute_projections.
--
-- Powers:
--   - Combined Heat tiebreaker in pick_cards.py (multi-signal hot hitters
--     get preferential leg selection when EV is close)
--   - /trends page Combined view (replaces the on-the-fly client compute
--     for historical comparison — current /trends still computes live)
--   - Player detail pages (show trend history over time)
--
-- Each trend_score is stored as a numeric ratio (NOT clamped) so that:
--   - The frontend can apply different clamps for different views
--   - We retain the raw signal for backtesting
--   - Tier classification is done client-side, not baked into the data
--
-- NULL values are intentional and meaningful — they mean the batter
-- didn't have enough data on that metric (e.g., season HR/PA = 0).
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS batter_trends (
    id                  SERIAL PRIMARY KEY,
    batter_id           INTEGER NOT NULL REFERENCES players(id),
    trend_date          DATE NOT NULL,

    -- Per-metric trend scores (raw, not clamped).
    -- ts = (L14 - season) / season, the same formula as useTrends.js.
    -- NULL when season value is below epsilon (no real baseline).
    hr_trend            NUMERIC(8, 4),
    hits_trend          NUMERIC(8, 4),
    barrel_trend        NUMERIC(8, 4),
    iso_trend           NUMERIC(8, 4),

    -- Combined Heat — geometric mean of (1 + clamped trends) - 1.
    -- Same math as useTrends.js computeCombinedTrend.
    -- NULL when fewer than 3 base metrics had valid trends.
    combined_trend      NUMERIC(8, 4),

    -- Tier classification baked in for fast filtering (BLAZING, HOT, WARM,
    -- FLAT, COOL, COLD, FROZEN). Computed from combined_trend.
    combined_tier       TEXT,

    -- Underlying sample sizes — useful for filtering low-trust signals
    -- (e.g., "only consider batters with ≥ 30 L14 PA in card selection")
    pa_l14              INTEGER,
    pa_season           INTEGER,

    -- Anchor metric used for the L14/SZN bar display when combined is
    -- shown. Usually 'hr', falls back to 'hits'/'barrel'/'iso' when HR is
    -- zero (slap hitters). Frontend can ignore this; it's mainly for
    -- backtesting consistency.
    anchor_metric       TEXT,

    -- Raw values for transparency / debugging
    hr_per_pa_l14       NUMERIC(7, 5),
    hr_per_pa_season    NUMERIC(7, 5),
    hit_per_pa_l14      NUMERIC(6, 4),
    hit_per_pa_season   NUMERIC(6, 4),
    barrel_pct_l14      NUMERIC(5, 2),
    barrel_pct_season   NUMERIC(5, 2),
    iso_l14             NUMERIC(5, 3),
    iso_season          NUMERIC(5, 3),

    updated_at          TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (batter_id, trend_date)
);

-- Common lookups
CREATE INDEX IF NOT EXISTS idx_batter_trends_date         ON batter_trends(trend_date);
CREATE INDEX IF NOT EXISTS idx_batter_trends_combined     ON batter_trends(combined_trend DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_batter_trends_tier         ON batter_trends(combined_tier) WHERE combined_tier IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_batter_trends_today_hot    ON batter_trends(trend_date, combined_trend DESC)
    WHERE combined_trend IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────
-- Helper view: today's heat board
--
-- Joins batter_trends with player metadata for one-shot consumption.
-- Card selection scripts can SELECT FROM this directly instead of
-- joining manually.
-- ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW batter_heat_today AS
SELECT
    bt.batter_id,
    p.name             AS batter_name,
    p.mlbam_id,
    p.bats,
    p.team_id,
    t.abbrev           AS team_abbrev,
    bt.combined_trend,
    bt.combined_tier,
    bt.hr_trend,
    bt.hits_trend,
    bt.barrel_trend,
    bt.iso_trend,
    bt.pa_l14,
    bt.pa_season,
    bt.anchor_metric,
    bt.trend_date
FROM batter_trends bt
JOIN players p ON p.id = bt.batter_id
LEFT JOIN teams t ON t.id = p.team_id
-- Use ET to define "today" — MLB games are scheduled in ET, and the
-- cron that populates this table runs at 2:13 AM ET. UTC would roll
-- over too early (at 8 PM ET) and leave the view empty during peak
-- East-Coast hours.
WHERE bt.trend_date = (NOW() AT TIME ZONE 'America/New_York')::date;

COMMENT ON TABLE batter_trends IS
  'Daily snapshot of multi-signal trend scores per batter. Powers Combined Heat in /trends and card-selection tiebreaking.';
