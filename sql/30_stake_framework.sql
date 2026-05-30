-- 30_stake_framework.sql
-- ────────────────────────────────────────────────────────────────────────
-- Tier-based stake sizing (tier_v1).
--
-- Tags each card/pod row with the stake-sizing regime that produced its
-- stake, so ROI views can separate the LEGACY leg-count regime (pre
-- 2026-05-30, stake keyed on leg count: $10/$5/$1) from the NEW
-- conviction-tier regime (stake keyed on suggested_stake_tier).
--
--   tier_v1 dollar ladder (1U = $100 on a $10,000 bankroll):
--     lock $200 (2U) · safe $100 (1U) · risky $25 (0.25U)
--     lottery $10 (0.1U) · donation $5 (0.05U)
--
-- stake_rec / pods.stake remain REAL DOLLARS — settle_cards.py denominates
-- realized P&L in them. The "U" concept lives only in the frontend display.
--
-- Forward-only ALTER, safe to re-run (IF NOT EXISTS). Historical rows keep
-- NULL (no backfill) — ROI queries filter `stake_framework = 'tier_v1'` to
-- compare like-for-like. 'leg_count_v1' is reserved so legacy rows CAN be
-- explicitly tagged later if a backfill is ever wanted.
-- ────────────────────────────────────────────────────────────────────────

ALTER TABLE cards
  ADD COLUMN IF NOT EXISTS stake_framework TEXT
    CHECK (stake_framework IS NULL OR
           stake_framework IN ('leg_count_v1', 'tier_v1'));

COMMENT ON COLUMN cards.stake_framework IS
  'Stake-sizing regime behind stake_rec. NULL = legacy leg-count ($10/$5/$1). '
  'tier_v1 = conviction-tier dollars sized by suggested_stake_tier '
  '(lock $200 / safe $100 / risky $25 / lottery $10 / donation $5; 1U=$100). '
  'leg_count_v1 reserved for explicit tagging of legacy rows.';

ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS stake_framework TEXT
    CHECK (stake_framework IS NULL OR
           stake_framework IN ('leg_count_v1', 'tier_v1'));

COMMENT ON COLUMN pods.stake_framework IS
  'See cards.stake_framework. NULL = legacy canonical $10 POD stake; '
  'tier_v1 = conviction-tier dollars written to pods.stake.';

-- ── Reversal (manual) ─────────────────────────────────────────────────────
-- ALTER TABLE cards DROP COLUMN IF EXISTS stake_framework;
-- ALTER TABLE pods  DROP COLUMN IF EXISTS stake_framework;
