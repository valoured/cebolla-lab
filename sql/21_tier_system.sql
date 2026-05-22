-- ════════════════════════════════════════════════════════════════════════
-- 21_tier_system.sql  ·  Tier-based picking framework metadata
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Adds columns to pods, card_legs, and projections to support the new
--   tier-based selection framework. Tier system replaces the multiplicative
--   combined_score with hierarchical priorities:
--
--     Tier 1 (dominant gates):  barrel%, xSLG, HR vs primary pitch (or
--                               hit rate, xBA, BvP for HRR market)
--     Tier 2 (confirming):      heat tier, HH%, contact score, BvP history
--     Tier 3 (tiebreakers):     edge, EV per dollar, platoon
--     Tier 4 (stake modifier):  park × weather (informational only)
--
--   Selection rule: must hit 2-of-3 Tier 1 thresholds to qualify. Score
--   accumulates from Tier 1 full-triple bonus + Tier 2 count bonuses.
--   Ties broken by Tier 3.
--
-- WHY
--   Multiplicative composite (edge × contact × heat) treats all signals
--   roughly equally. Tier system encodes the observation that some signals
--   are predictive gates (must-have) and others are nudges (tiebreakers).
--
-- IDEMPOTENCY
--   All ADD COLUMN statements use IF NOT EXISTS. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════


-- ──── PODs ────────────────────────────────────────────────────────────────
ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS tier1_hits        INTEGER,    -- count of Tier 1 thresholds passed (0-3)
  ADD COLUMN IF NOT EXISTS tier2_hits        INTEGER,    -- count of Tier 2 thresholds passed (0-4)
  ADD COLUMN IF NOT EXISTS tier_score        NUMERIC(5, 3),  -- final composite (1.0 base + bonuses)
  ADD COLUMN IF NOT EXISTS stake_modifier    NUMERIC(4, 3),  -- park × weather (clamped 0.7-1.3)
  ADD COLUMN IF NOT EXISTS tier_metadata     JSONB;     -- structured detail for debugging/display

COMMENT ON COLUMN pods.tier1_hits IS
  'Count of Tier 1 thresholds the pick passed at lock time (0-3). Picks need ≥2 to qualify.';
COMMENT ON COLUMN pods.tier2_hits IS
  'Count of Tier 2 confirming signals at lock time (0-4).';
COMMENT ON COLUMN pods.tier_score IS
  'Composite score used to rank Tier-1-qualifying picks. 1.0 base + 0.10 for full triple + 0.05 per Tier 2 hit (cap 0.15).';
COMMENT ON COLUMN pods.stake_modifier IS
  'Park × weather composite for informational stake recommendation. 1.0 = neutral; 0.7-1.3 range. NOT applied to projection.';
COMMENT ON COLUMN pods.tier_metadata IS
  'JSONB structure: {tier1: {barrel: 0.15, xslg: 0.62, hr_vs_pitch: 0.10, passed: 3}, tier2: {...}, conditions: {park: 1.05, wind: "out_8mph"}}';


-- ──── Card legs ───────────────────────────────────────────────────────────
ALTER TABLE card_legs
  ADD COLUMN IF NOT EXISTS tier1_hits        INTEGER,
  ADD COLUMN IF NOT EXISTS tier2_hits        INTEGER,
  ADD COLUMN IF NOT EXISTS tier_score        NUMERIC(5, 3),
  ADD COLUMN IF NOT EXISTS stake_modifier    NUMERIC(4, 3),
  ADD COLUMN IF NOT EXISTS tier_metadata     JSONB;

COMMENT ON COLUMN card_legs.tier_score IS 'See pods.tier_score — same semantics, per leg.';


-- ──── Cards (aggregate stake modifier across legs) ────────────────────────
ALTER TABLE cards
  ADD COLUMN IF NOT EXISTS avg_stake_modifier NUMERIC(4, 3);

COMMENT ON COLUMN cards.avg_stake_modifier IS
  'Average stake_modifier across all legs. Display as: 0.95 → "neutral conditions"; 1.15 → "favorable +15%"; 0.80 → "cautious -20%".';


-- ──── Projections (split prob into pure vs market-aware) ──────────────────
-- projected_prob remains the source-of-truth for picker math (Statcast +
-- matchup only, NO park/weather). park_adj and weather_adj are stored
-- separately so pickers can roll them into stake_modifier without
-- contaminating the projection.
ALTER TABLE projections
  ADD COLUMN IF NOT EXISTS stake_modifier    NUMERIC(4, 3);

COMMENT ON COLUMN projections.stake_modifier IS
  'park_adj × weather_adj, clamped to [0.7, 1.3]. Surfaced as informational stake recommendation on POD/Cards. Not applied to projected_prob.';


-- ──── Indices ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_pods_tier_score
  ON pods(tier_score DESC)
  WHERE tier_score IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_card_legs_tier_score
  ON card_legs(tier_score DESC)
  WHERE tier_score IS NOT NULL;
