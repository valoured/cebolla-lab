-- ════════════════════════════════════════════════════════════════════════
-- 20_calibration.sql  ·  Closing Line Value (CLV) tracking
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Adds 4 columns to each of `pods` and `card_legs`:
--     - closing_odds         INTEGER       — DK american odds captured ~30min before first pitch
--     - closing_implied      NUMERIC(6,4)  — raw implied prob from closing_odds (before de-vig)
--     - closing_no_vig       NUMERIC(6,4)  — de-vigged implied prob (where market structure allows)
--     - closing_captured_at  TIMESTAMPTZ   — when we captured the closing odds
--     - clv_raw              NUMERIC(7,5)  — closing_implied - lock_implied (positive = we beat the close)
--     - clv_no_vig           NUMERIC(7,5)  — closing_no_vig  - lock_no_vig  (preferred CLV signal)
--
-- WHY
--   CLV is the gold-standard short-term metric for betting model quality.
--   Even before a single bet settles, a consistently positive CLV means we
--   locked at prices the market subsequently moved AWAY from — strong evidence
--   our model is finding mispricing. Settled W/L is high-variance and takes
--   months to stabilize. CLV stabilizes much faster (typically 50-100 picks).
--
--   Specifically lets us answer:
--     - Did the arsenal v2 math actually move us to better-priced picks?
--     - Are HR PODs at +1000 longshots producing positive CLV (real edge)
--       or negative CLV (the books were just slow to update)?
--     - Are 4-leg cards finding genuine market inefficiency or just variance?
--
-- IDEMPOTENCY
--   All ADD COLUMN statements use IF NOT EXISTS. Safe to re-run.
--
-- ROLLBACK
--   Columns can be dropped if needed:
--     ALTER TABLE pods       DROP COLUMN closing_odds, DROP COLUMN closing_implied,
--                            DROP COLUMN closing_no_vig, DROP COLUMN closing_captured_at,
--                            DROP COLUMN clv_raw, DROP COLUMN clv_no_vig;
--     ALTER TABLE card_legs  DROP COLUMN ... (same list);
--   No FK references, no cascading dependencies — safe to drop.
-- ════════════════════════════════════════════════════════════════════════


-- ──── PODs ────────────────────────────────────────────────────────────────
ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS closing_odds         INTEGER,
  ADD COLUMN IF NOT EXISTS closing_implied      NUMERIC(6, 4),
  ADD COLUMN IF NOT EXISTS closing_no_vig       NUMERIC(6, 4),
  ADD COLUMN IF NOT EXISTS closing_captured_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS clv_raw              NUMERIC(7, 5),
  ADD COLUMN IF NOT EXISTS clv_no_vig           NUMERIC(7, 5);

COMMENT ON COLUMN pods.closing_odds IS
  'DraftKings american odds captured ~30 minutes before first pitch. NULL means closing odds were never captured (game already started, or pull_closing_odds failed).';
COMMENT ON COLUMN pods.closing_implied IS
  'Raw implied probability from closing_odds (american_to_implied). Positive odds: 100/(odds+100). Negative: |odds|/(|odds|+100).';
COMMENT ON COLUMN pods.closing_no_vig IS
  'De-vigged closing implied probability. For HR Anytime (one-sided market) we approximate using a standard 4.5% overround. For Hits/HRR (two-sided), uses both legs to de-vig properly.';
COMMENT ON COLUMN pods.clv_raw IS
  'closing_implied - lock_implied. Positive = we locked at a better price than the close.';
COMMENT ON COLUMN pods.clv_no_vig IS
  'closing_no_vig - no_vig_prob (lock-time de-vigged). Preferred CLV measure. Positive = our locked price was sharper than where the market settled.';


-- ──── Card legs ───────────────────────────────────────────────────────────
ALTER TABLE card_legs
  ADD COLUMN IF NOT EXISTS closing_odds         INTEGER,
  ADD COLUMN IF NOT EXISTS closing_implied      NUMERIC(6, 4),
  ADD COLUMN IF NOT EXISTS closing_no_vig       NUMERIC(6, 4),
  ADD COLUMN IF NOT EXISTS closing_captured_at  TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS clv_raw              NUMERIC(7, 5),
  ADD COLUMN IF NOT EXISTS clv_no_vig           NUMERIC(7, 5);

COMMENT ON COLUMN card_legs.closing_odds IS 'See pods.closing_odds — same semantics.';
COMMENT ON COLUMN card_legs.clv_no_vig IS 'See pods.clv_no_vig — same semantics.';


-- ──── Indices ─────────────────────────────────────────────────────────────
-- Index on clv_no_vig for the Calibration panel's queries (rolling averages,
-- distribution histograms). Only index non-null rows since most rows pre-
-- backfill will be NULL.
CREATE INDEX IF NOT EXISTS idx_pods_clv_no_vig
  ON pods(clv_no_vig)
  WHERE clv_no_vig IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_card_legs_clv_no_vig
  ON card_legs(clv_no_vig)
  WHERE clv_no_vig IS NOT NULL;

-- Index on closing_captured_at for time-window queries
-- ("CLV from last 30 days" needs to filter by pod_date or capture time)
CREATE INDEX IF NOT EXISTS idx_pods_closing_captured
  ON pods(closing_captured_at DESC)
  WHERE closing_captured_at IS NOT NULL;
