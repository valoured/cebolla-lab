-- ────────────────────────────────────────────────────────────────
-- Migration 16: Cleanup is_current bloat in odds_snapshots
--
-- Bug: pull_dk_odds.py was inserting every snapshot with is_current=TRUE
-- without flipping prior rows to FALSE. Over weeks/months this left tens
-- of thousands of stale rows flagged "current".
--
-- This migration fixes the existing data by marking only the LATEST
-- snapshot per (game_id, player_id, market, book, line) tuple as current,
-- and stales everything else.
--
-- Safe to re-run (idempotent: already-stale rows stay stale, only-current
-- rows stay current).
--
-- Expected impact:
--   Before: ~41,000 is_current=TRUE rows
--   After:  ~3,000 is_current=TRUE rows (1 per active line)
-- ────────────────────────────────────────────────────────────────

-- Mark everything as stale first
UPDATE odds_snapshots
SET is_current = FALSE
WHERE is_current = TRUE;

-- Then flip ONLY the latest per (game, player, market, book, line) to current.
-- Latest = max(snapshot_time) within that group.
WITH latest AS (
  SELECT DISTINCT ON (game_id, player_id, market, book, line) id
  FROM odds_snapshots
  ORDER BY game_id, player_id, market, book, line, snapshot_time DESC
)
UPDATE odds_snapshots
SET is_current = TRUE
WHERE id IN (SELECT id FROM latest);

-- Sanity check: per-market counts should now be ~89 (hr/hits) or ~69*5 (hrr)
-- Run separately:
--   SELECT market, COUNT(*) AS current_rows, COUNT(DISTINCT player_id) AS players
--   FROM odds_snapshots WHERE is_current = TRUE
--   GROUP BY market ORDER BY market;
