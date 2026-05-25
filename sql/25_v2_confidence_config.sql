-- ════════════════════════════════════════════════════════════════════════
-- 25_v2_confidence_config.sql  ·  Patch 3 market_context + Patch 9 weights
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   1. Adds `market_context` JSONB to pods AND cards (Patch 3). This is a
--      DISPLAY contract, kept SEPARATE from tier_metadata (framework internals)
--      so the frontend can read cebolla_edge / ev_chart / edge-warning without
--      parsing scoring internals. No effect on tier/stake/confidence.
--   2. Seeds the 9 Patch 9 confidence-formula weights + flag bonuses into
--      model_thresholds (honors "everything tunable — no exceptions"; these are
--      the first knobs to turn once calibration data arrives).
--   3. Rewrites the wind/temp coefficient row descriptions to mark them
--      reference-only (live computation lives in pull_weather.py — see the
--      Option B wind/temp resolution; pick_pod.py does NOT read them).
--
-- STACK BONUS — SINGLE SOURCE OF TRUTH
--   There is intentionally NO conf_stack_bonus row. The existing stack_boost
--   row (0.10, seeded in migration 23) is shared by BOTH Patch 5 stack
--   detection AND Patch 9's confidence contribution. calculate_confidence
--   reads stack_boost directly. (Patch 9's "0.08" was summary drift; 0.10 is
--   the Patch 5 spec value and matches the other strong-signal bonuses.)
--
-- IDEMPOTENT — ADD COLUMN IF NOT EXISTS; INSERT ... ON CONFLICT DO NOTHING;
-- UPDATE is naturally idempotent. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════


-- ──── 1. Patch 3 · market_context (display-only) ──────────────────────────
ALTER TABLE pods  ADD COLUMN IF NOT EXISTS market_context JSONB;
ALTER TABLE cards ADD COLUMN IF NOT EXISTS market_context JSONB;

COMMENT ON COLUMN pods.market_context IS
  'Patch 3: DISPLAY-ONLY market context (cebolla_edge, ev_chart, edge-warning string). Separate contract from tier_metadata (framework internals). No tier/stake/confidence effect.';
COMMENT ON COLUMN cards.market_context IS
  'See pods.market_context — display-only, separate from tier_metadata.';


-- ──── 2. Patch 9 · confidence-formula weights + flag bonuses ──────────────
-- Confidence formula (calculate_confidence):
--   base   = min(primary_signal * conf_primary_signal_mult, conf_base_cap)
--   tier2  = min(tier2_hits * conf_tier2_signal_weight, conf_tier2_cap)
--   flags  = conf_catcher_bonus (if catcher gate) + near_miss_boost (if near-miss)
--            + stack_boost (if stacked leg, cards) + conviction bonus (user flag)
--   final  = clamp(base + tier2 + flags, 0, 1)        # environment does NOT contribute
INSERT INTO model_thresholds (key, num_value, text_value, market_class, unit, description) VALUES
  ('conf_primary_signal_mult', 4.0,  NULL, NULL, 'coef',    'Patch 9: primary_signal × this = base confidence contribution (before cap).'),
  ('conf_base_cap',            0.40, NULL, NULL, 'decimal', 'Patch 9: hard cap on the base contribution → min(primary_signal*mult, cap).'),
  ('conf_tier2_signal_weight', 0.06, NULL, NULL, 'decimal', 'Patch 9: confidence added per Tier 2 signal hit.'),
  ('conf_tier2_cap',           0.30, NULL, NULL, 'decimal', 'Patch 9: max total Tier 2 confidence contribution.'),
  ('conf_catcher_bonus',       0.10, NULL, NULL, 'decimal', 'Patch 9/1: confidence bump when the catcher-promotion gate fires (orthogonal to the +1 tier_boost on tier_score).'),
  ('conf_user_flag_gut',       0.07, NULL, NULL, 'decimal', 'Patch 7/9: confidence bump for a gut-conviction user flag.'),
  ('conf_user_flag_matchup',   0.10, NULL, NULL, 'decimal', 'Patch 7/9: confidence bump for a matchup-conviction user flag.'),
  ('conf_user_flag_hot_streak',0.08, NULL, NULL, 'decimal', 'Patch 7/9: confidence bump for a hot_streak-conviction user flag.'),
  ('user_flag_lottery_stake',  0.4,  NULL, NULL, 'decimal', 'Patch 7: stake_modifier for an unsurfaced flagged batter added as a C+ lottery pick.')
ON CONFLICT (key) DO NOTHING;
-- NOTE: stacked-leg confidence reuses the existing stack_boost row (0.10).
-- NOTE: near-miss confidence reuses the existing near_miss_boost row (0.05).


-- ──── 3. Self-document the wind/temp rows as reference-only ───────────────
-- These remain in the table for magnitude documentation; pick_pod.py's
-- Option B environment math does NOT read them (wind/temp are baked into
-- park_adj by pull_weather.py — the live computation).
UPDATE model_thresholds
   SET description = 'Reference value only — live wind/temp computation is in pull_weather.py (baked into park_adj). pick_pod.py does NOT read this; see calculate_game_environment (Option B: park_adj × humidity × elevation).'
 WHERE key IN ('wind_out_coef_per_mph', 'wind_in_coef_per_mph', 'temp_coef_per_deg_f');

-- ════════════════════════════════════════════════════════════════════════
-- END 25_v2_confidence_config.sql
-- ════════════════════════════════════════════════════════════════════════
