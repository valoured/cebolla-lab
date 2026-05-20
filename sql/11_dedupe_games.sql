-- ────────────────────────────────────────────────────────────────
-- One-shot cleanup: remove duplicate game rows.
--
-- pull_schedule.py created some games twice with different `id` values
-- but the same `mlb_game_pk`. The duplicate rows broke pull_lineups
-- because they share home/away team IDs and the script tried to
-- insert overlapping (game_id, team_id, batting_order) rows.
--
-- Strategy: for each set of duplicates (grouped by mlb_game_pk),
-- KEEP the lowest-ID row (the original) and DELETE the rest. Any
-- foreign-key dependencies (lineups, projections, odds, scores)
-- pointing at the higher-ID duplicates need to be cleaned up first.
-- ────────────────────────────────────────────────────────────────

-- 1. Identify the duplicate set: which mlb_game_pk values have >1 row,
--    and which ID(s) should be deleted (keep the lowest).
WITH dupes AS (
  SELECT mlb_game_pk, MIN(id) AS keep_id, ARRAY_AGG(id ORDER BY id) AS all_ids
  FROM games
  WHERE mlb_game_pk IS NOT NULL
  GROUP BY mlb_game_pk
  HAVING COUNT(*) > 1
),
to_delete AS (
  SELECT unnest(all_ids) AS bad_id, keep_id
  FROM dupes
)
SELECT bad_id, keep_id FROM to_delete WHERE bad_id != keep_id;
-- Run this SELECT first to verify what will be removed.

-- 2. Once verified, actually delete dependent rows then the duplicates:
-- Uncomment and run after reviewing the SELECT above.

-- DELETE FROM lineups WHERE game_id IN (
--   SELECT id FROM games g
--   WHERE EXISTS (
--     SELECT 1 FROM games g2
--     WHERE g2.mlb_game_pk = g.mlb_game_pk AND g2.id < g.id
--   )
-- );
--
-- DELETE FROM projections WHERE game_id IN (
--   SELECT id FROM games g
--   WHERE EXISTS (
--     SELECT 1 FROM games g2
--     WHERE g2.mlb_game_pk = g.mlb_game_pk AND g2.id < g.id
--   )
-- );
--
-- DELETE FROM odds_snapshots WHERE game_id IN (
--   SELECT id FROM games g
--   WHERE EXISTS (
--     SELECT 1 FROM games g2
--     WHERE g2.mlb_game_pk = g.mlb_game_pk AND g2.id < g.id
--   )
-- );
--
-- DELETE FROM games g
-- WHERE EXISTS (
--   SELECT 1 FROM games g2
--   WHERE g2.mlb_game_pk = g.mlb_game_pk AND g2.id < g.id
-- );

-- 3. Add a unique constraint to prevent this from happening again.
-- Will fail if duplicates still exist, which is the point.
-- ALTER TABLE games ADD CONSTRAINT games_mlb_game_pk_unique UNIQUE (mlb_game_pk);
