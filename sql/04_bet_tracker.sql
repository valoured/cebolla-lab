-- ============================================================
-- CEBOLLA LABORATORY — Schema Migration 04
-- Phase 5: Bet Tracker improvements
-- Adds parlay support + convenience view
-- Run in Supabase SQL Editor (additive, idempotent)
-- ============================================================

-- Add parlay_id to existing bet_log
ALTER TABLE bet_log
  ADD COLUMN IF NOT EXISTS parlay_id   TEXT,            -- shared id across legs of one parlay
  ADD COLUMN IF NOT EXISTS parlay_legs INTEGER,         -- total legs in this parlay (denorm for convenience)
  ADD COLUMN IF NOT EXISTS settled_at  TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_bet_log_parlay ON bet_log(parlay_id) WHERE parlay_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bet_log_pending ON bet_log(result) WHERE result = 'pending';

-- Convenience view: bets with player names + game info pre-joined
CREATE OR REPLACE VIEW bet_log_enriched AS
SELECT
    b.*,
    p.name                        AS player_name,
    p.mlbam_id                    AS player_mlbam,
    p.is_pitcher                  AS player_is_pitcher,
    g.game_date,
    g.game_time_utc,
    g.status                      AS game_status,
    at.abbrev                     AS away_abbrev,
    ht.abbrev                     AS home_abbrev,
    pt.id                         AS player_team_id,
    pt.abbrev                     AS player_team_abbrev
FROM bet_log b
LEFT JOIN players p   ON p.id = b.player_id
LEFT JOIN games   g   ON g.id = b.game_id
LEFT JOIN teams   at  ON at.id = g.away_team_id
LEFT JOIN teams   ht  ON ht.id = g.home_team_id
LEFT JOIN teams   pt  ON pt.id = p.team_id;

-- Extended ROI view: by edge bucket + by model version
CREATE OR REPLACE VIEW roi_by_edge_bucket AS
SELECT
    CASE
        WHEN edge_at_placement >= 0.05  THEN '5%+ edge'
        WHEN edge_at_placement >= 0.03  THEN '3-5% edge'
        WHEN edge_at_placement >= 0.01  THEN '1-3% edge'
        WHEN edge_at_placement >= 0     THEN '0-1% edge'
        ELSE 'negative edge'
    END                                                       AS bucket,
    COUNT(*)                                                  AS bets,
    SUM(stake)                                                AS total_staked,
    SUM(COALESCE(pnl, 0))                                     AS total_pnl,
    ROUND(SUM(COALESCE(pnl, 0)) / NULLIF(SUM(stake), 0), 4)   AS roi,
    ROUND(
      COUNT(*) FILTER (WHERE result = 'win')::numeric
      / NULLIF(COUNT(*) FILTER (WHERE result IN ('win','loss')), 0),
      4
    )                                                         AS win_rate
FROM bet_log
WHERE result IN ('win', 'loss', 'push')
GROUP BY 1
ORDER BY MIN(edge_at_placement) DESC;

-- Per-model ROI (so you can compare v0.1.1 vs v0.1.2 vs v0.1.3 etc.)
CREATE OR REPLACE VIEW roi_by_model_version AS
SELECT
    model_version,
    COUNT(*)                                                  AS bets,
    SUM(stake)                                                AS total_staked,
    SUM(COALESCE(pnl, 0))                                     AS total_pnl,
    ROUND(SUM(COALESCE(pnl, 0)) / NULLIF(SUM(stake), 0), 4)   AS roi,
    ROUND(
      COUNT(*) FILTER (WHERE result = 'win')::numeric
      / NULLIF(COUNT(*) FILTER (WHERE result IN ('win','loss')), 0),
      4
    )                                                         AS win_rate
FROM bet_log
WHERE result IN ('win', 'loss', 'push') AND model_version IS NOT NULL
GROUP BY model_version
ORDER BY model_version DESC;
