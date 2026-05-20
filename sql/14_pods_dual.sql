-- ────────────────────────────────────────────────────────────────
-- Migration 14: Dual POD support — allow two PODs per slate (HR + HRR).
--
-- Original schema enforced UNIQUE(pod_date) which prevented running a second
-- POD picker for a different market. This migration:
--   1. Drops the single-pod-per-day unique constraint
--   2. Adds market_class column (defaults 'hr' — backfills existing rows)
--   3. Adds new composite UNIQUE(pod_date, market_class) so one POD per
--      market per day is enforced going forward
--
-- Safe to run on a populated table. Existing rows backfill to 'hr' so all
-- historical PODs remain attributed to the HR market they came from.
-- ────────────────────────────────────────────────────────────────

-- Step 1: Add the column with a default so existing rows backfill cleanly
ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS market_class TEXT NOT NULL DEFAULT 'hr';

-- Step 2: Drop the old single-pod-per-day constraint.
-- The constraint name follows Postgres convention; the IF EXISTS guard
-- makes this idempotent in case it was already dropped or named differently.
ALTER TABLE pods
  DROP CONSTRAINT IF EXISTS pods_pod_date_key;

-- Step 3: Add the new composite constraint. One POD per market per day.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'pods_unique_per_market'
  ) THEN
    ALTER TABLE pods
      ADD CONSTRAINT pods_unique_per_market UNIQUE (pod_date, market_class);
  END IF;
END $$;

-- Step 4: Index for queries that filter by market_class (e.g. "all HR pods this month")
CREATE INDEX IF NOT EXISTS idx_pods_market_class ON pods(market_class);
CREATE INDEX IF NOT EXISTS idx_pods_date_market_desc ON pods(pod_date DESC, market_class);

-- Documentation
COMMENT ON COLUMN pods.market_class IS
  'High-level market category for this POD: ''hr'' (Home Run anytime) or '
  '''hrr'' (Hits + Runs + RBIs, line set per pick). One POD per market_class '
  'per pod_date enforced by pods_unique_per_market.';
