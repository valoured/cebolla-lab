-- ────────────────────────────────────────────────────────────────
-- Migration 29: card_market column for market-segregated card buckets.
--
-- The Phase 1.2 picker shipped a single uncapped multi-leg stream + a
-- per-game straight phase, grouped on the frontend by leg-count `tier`
-- (straight / two_leg / three_leg / four_leg). The market-segregated
-- rebuild instead fills four buckets per slate:
--
--   hr   → 7-8 cards   (legs: hr_anytime)
--   hrr  → 6-7 cards   (legs: h_r_rbi_1.5 OR h_r_rbi_2.5)
--   hits → 6-7 cards   (legs: hits_yes)
--   mix  → max 3 cards (cross-market: >= 2 distinct families per card)
--
-- Each bucket holds a SCORE-RANKED mix of single-leg straights and
-- 2/3/4-leg market-pure parlays competing head-to-head for slots.
--
-- DESIGN — Option A (separation of concerns):
--   `tier`        stays = leg-count  (straight/two_leg/three_leg/four_leg)
--   `card_market` NEW   = bucket label (hr/hrr/hits/mix)
--
-- This is purely additive: the existing CardsView.vue grouping (by tier)
-- and settle_cards.py (tier as fallback label) keep working unchanged.
-- A frontend cutover to bucket-grouped display is a separate future task.
--
-- rbi_yes legs are NOT bucketed (ignored by this rebuild); if RBI becomes
-- its own bucket later that is a separate migration + picker change.
-- ────────────────────────────────────────────────────────────────

ALTER TABLE cards
  ADD COLUMN IF NOT EXISTS card_market TEXT
    CHECK (card_market IS NULL OR card_market IN ('hr', 'hrr', 'hits', 'mix'));

CREATE INDEX IF NOT EXISTS idx_cards_card_market ON cards(card_market);

COMMENT ON COLUMN cards.card_market IS
  'Market-segregated bucket: hr | hrr (h_r_rbi_1.5 + h_r_rbi_2.5) | hits | '
  'mix (cross-market, >=2 distinct families). Independent of `tier`, which '
  'stays = leg-count. NULL for legacy pre-migration-29 cards.';
