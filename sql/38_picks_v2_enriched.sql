-- ════════════════════════════════════════════════════════════════════════
-- 38_picks_v2_enriched.sql  ·  v2 Day 6 — Shadow Lab frontend read view
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   picks_v2_enriched: picks_v2 with player name + game/team context pre-joined,
--   plus a single `outcome_status` label the frontend colors rows by directly.
--   Mirrors the bet_log_enriched convention (sql/04) — the frontend reads
--   enriched VIEWS, not raw tables with client-side joins.
--
-- WHY A GRANT
--   Base tables (picks_v2, players, games, teams) are already anon-readable, but
--   a VIEW does not inherit that — anon needs an explicit SELECT grant or the
--   Shadow Lab page (anon key) gets an empty/403 result. GRANT is idempotent.
--
--   CREATE OR REPLACE VIEW → IDEMPOTENT, safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW picks_v2_enriched AS
SELECT
    pv.*,
    p.name                        AS player_name,
    p.mlbam_id                    AS player_mlbam,
    p.bats                        AS player_bats,
    g.game_date,
    g.game_time_utc,
    g.status                      AS game_status,
    at.abbrev                     AS away_abbrev,
    ht.abbrev                     AS home_abbrev,
    pt.id                         AS player_team_id,
    pt.abbrev                     AS player_team_abbrev,
    -- Single settlement label for direct row coloring in the frontend.
    --   hr           → batter hit 1+ HR        (green)
    --   no_hr        → played, no HR            (red)
    --   did_not_play → scratch / 0-PA sub       (muted, book-void)
    --   game_void    → postponed / cancelled    (muted)
    --   pending      → game not yet settled
    CASE
        WHEN pv.book_settled_outcome IS TRUE         THEN 'hr'
        WHEN pv.book_settled_outcome IS FALSE        THEN 'no_hr'
        WHEN (pv.warnings->>'did_not_play') = 'true' THEN 'did_not_play'
        WHEN (pv.warnings->>'game_void')    = 'true' THEN 'game_void'
        ELSE 'pending'
    END                           AS outcome_status
FROM picks_v2 pv
LEFT JOIN players p  ON p.id  = pv.player_id
LEFT JOIN games   g  ON g.id  = pv.game_id
LEFT JOIN teams   at ON at.id = g.away_team_id
LEFT JOIN teams   ht ON ht.id = g.home_team_id
LEFT JOIN teams   pt ON pt.id = p.team_id;

-- Frontend uses the anon key; views need the grant explicitly.
GRANT SELECT ON picks_v2_enriched TO anon, authenticated;

COMMENT ON VIEW picks_v2_enriched IS
  'Shadow Lab read view: picks_v2 + player/game/team context + outcome_status '
  'label. Anon-readable. Mirrors bet_log_enriched. Frontend filters '
  'longshot_unrated / warnings client-side — this view returns the full slate.';
