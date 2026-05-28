-- ════════════════════════════════════════════════════════════════════════
-- 28_phase1_matchup_first.sql  ·  Phase 1: matchup-first POD ranking
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Schema + tunable thresholds for the Phase 1 picker rewrite
--   (pick_pod.py + pick_cards.py). The v1 tier_system gates and v2
--   confidence_score are replaced by:
--
--     · Two-gate eligibility:
--         Gate A (opportunity): season_pa >= 120 AND pitch_type_pa >= 20
--           (pitch_type_pa = SUM of by_pitch_type PA across the family of
--            the opposing pitcher's primary pitch — family-aggregated)
--         Gate B (matchup exception): bvp.ab >= 8
--           AND (bvp.hr >= 2 OR (bvp.avg >= .300 AND bvp.hr >= 1))
--           AND (season barrel_pct >= 8.0 OR season xslg >= .430)
--         Hard exclusion: season_pa < 50 → never eligible.
--
--     · primary_signal ∈ ~[0, 1] = max of three components:
--         observed_vs_pitcher    = bvp.hr / bvp.ab    (if bvp.ab >= 8)
--         observed_vs_pitch_type = by_pitch_type[primary].hr_pct / 100
--                                  (specific pitch — NOT family-averaged —
--                                   gated by family-summed pa >= 20 for
--                                   reliability)
--         recent_power_form      = l7.xslg / 2.0       (if l7.pa >= 10;
--                                                       falls back to L14)
--       Source label persisted so we can audit which signal won.
--
--     · EV demoted from hard floor to a screen on the suggested stake tier:
--         edge >= 0.03        → "full" (no demote)
--         0.0 <= edge < 0.03  → "drop" (suggested_stake_tier one step worse)
--         -0.10 <= edge < 0.0 → "warn_drop" (drop + warning string)
--         edge < -0.10        → "disqualify" (candidate dropped entirely)
--
--     · Anchor unification across HR + HRR:
--         eligibility + primary_signal computed ONCE per batter
--         (market-agnostic). Top anchor expressed across HR + HRR markets;
--         each market still passes its own EV screen.
--
--     · Heat removed from the picker (compute_batter_trends keeps running
--       for the frontend).
--
-- WHAT THIS DOES NOT TOUCH
--   v1/v2 columns (tier_score, tier1_hits, tier2_hits, confidence_score,
--   confidence_tier, tier_metadata, combined_score, market_context) are
--   LEFT IN PLACE. Phase 1 picks write NULL into the v1/v2-only columns,
--   EXCEPT confidence_score: per agreed back-compat plan, we ALSO write
--   primary_signal into confidence_score (pods + cards + card_legs) so the
--   frontend keeps rendering until it migrates to read primary_signal.
--   Drop the dual-write in a follow-up after the frontend cutover.
--
-- IDEMPOTENCY
--   ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS / INSERT ... ON
--   CONFLICT DO NOTHING. Safe to re-run; tuned threshold values are never
--   overwritten by a re-run.
-- ════════════════════════════════════════════════════════════════════════


-- ──── Persisted columns: pods ─────────────────────────────────────────────
ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS primary_signal        NUMERIC(6, 5),
  ADD COLUMN IF NOT EXISTS primary_signal_source TEXT
    CHECK (primary_signal_source IS NULL OR primary_signal_source IN
           ('bvp_observed','pitch_type_observed','l7_power_form','l14_power_form')),
  ADD COLUMN IF NOT EXISTS suggested_stake_tier  TEXT
    CHECK (suggested_stake_tier IS NULL OR suggested_stake_tier IN
           ('lock','safe','risky','lottery','donation')),
  ADD COLUMN IF NOT EXISTS phase1_metadata       JSONB;

COMMENT ON COLUMN pods.primary_signal IS
  'Phase 1 ranking signal in ~[0, 1]. max(bvp.hr/bvp.ab, by_pitch_type[primary].hr_pct/100, l7.xslg/2). Reused by frontend back-compat into confidence_score until UI migrates.';
COMMENT ON COLUMN pods.primary_signal_source IS
  'Which component produced the max — bvp_observed | pitch_type_observed | l7_power_form | l14_power_form. NULL only when all three components were unavailable.';
COMMENT ON COLUMN pods.suggested_stake_tier IS
  'Phase 1 advisory tier — DISPLAY ONLY, NOT applied to stake yet. lock >= 0.65 / safe >= 0.50 / risky >= 0.30 / lottery >= 0.15 / donation < 0.15. EV "drop"/"warn_drop" bumps one step worse.';
COMMENT ON COLUMN pods.phase1_metadata IS
  'Phase 1 forensics namespace, separate from tier_metadata. Keys: gate (A|B|null), ev_action, ev_warning, eligibility_detail, primary_signal_components, primary_signal_source.';


-- ──── Persisted columns: cards ────────────────────────────────────────────
ALTER TABLE cards
  ADD COLUMN IF NOT EXISTS primary_signal        NUMERIC(6, 5),
  ADD COLUMN IF NOT EXISTS primary_signal_source TEXT
    CHECK (primary_signal_source IS NULL OR primary_signal_source IN
           ('bvp_observed','pitch_type_observed','l7_power_form','l14_power_form')),
  ADD COLUMN IF NOT EXISTS suggested_stake_tier  TEXT
    CHECK (suggested_stake_tier IS NULL OR suggested_stake_tier IN
           ('lock','safe','risky','lottery','donation')),
  ADD COLUMN IF NOT EXISTS phase1_metadata       JSONB;

COMMENT ON COLUMN cards.primary_signal IS
  'Phase 1 card-level aggregate = mean(leg.primary_signal). Reused by frontend back-compat into confidence_score until UI migrates.';
COMMENT ON COLUMN cards.primary_signal_source IS
  'Aggregated across legs — not a clean single source label. Stored NULL today; per-leg source on card_legs.primary_signal_source.';
COMMENT ON COLUMN cards.suggested_stake_tier IS
  'See pods.suggested_stake_tier. Card-level tier derived from card_confidence + worst-leg EV action.';
COMMENT ON COLUMN cards.phase1_metadata IS
  'Phase 1 forensics namespace. Card-level summary: per_leg = [{player_id, gate, ev_action, primary_signal, primary_signal_source, ...}], plus card aggregates.';


-- ──── Persisted columns: card_legs ────────────────────────────────────────
ALTER TABLE card_legs
  ADD COLUMN IF NOT EXISTS primary_signal        NUMERIC(6, 5),
  ADD COLUMN IF NOT EXISTS primary_signal_source TEXT
    CHECK (primary_signal_source IS NULL OR primary_signal_source IN
           ('bvp_observed','pitch_type_observed','l7_power_form','l14_power_form')),
  ADD COLUMN IF NOT EXISTS suggested_stake_tier  TEXT
    CHECK (suggested_stake_tier IS NULL OR suggested_stake_tier IN
           ('lock','safe','risky','lottery','donation')),
  ADD COLUMN IF NOT EXISTS phase1_metadata       JSONB;

COMMENT ON COLUMN card_legs.primary_signal IS
  'See pods.primary_signal — per-leg. Reused by back-compat into card_legs.confidence_score until UI migrates.';
COMMENT ON COLUMN card_legs.phase1_metadata IS
  'See pods.phase1_metadata — per-leg forensics (gate, ev_action, eligibility_detail, primary_signal_components, primary_signal_source).';


-- ──── Ranking indices (mirror existing tier_score / confidence indices) ──
CREATE INDEX IF NOT EXISTS idx_pods_primary_signal
  ON pods(primary_signal DESC) WHERE primary_signal IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cards_primary_signal
  ON cards(primary_signal DESC) WHERE primary_signal IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_card_legs_primary_signal
  ON card_legs(primary_signal DESC) WHERE primary_signal IS NOT NULL;


-- ════════════════════════════════════════════════════════════════════════
-- Tunable thresholds (model_thresholds) — 20 new rows
-- ════════════════════════════════════════════════════════════════════════
INSERT INTO model_thresholds (key, num_value, text_value, market_class, unit, description) VALUES

  -- ── Eligibility · hard exclusion ────────────────────────────────────────
  ('eligibility_season_pa_hard_min',         50,    NULL, NULL, 'count',
     'Phase 1: hard exclusion — season_pa below this is never eligible regardless of gate.'),

  -- ── Eligibility · Gate A (opportunity) ──────────────────────────────────
  ('eligibility_gate_a_season_pa_min',       120,   NULL, NULL, 'count',
     'Phase 1 Gate A: minimum season plate appearances for the opportunity gate.'),
  ('eligibility_gate_a_pitch_type_pa_min',   20,    NULL, NULL, 'count',
     'Phase 1 Gate A: minimum FAMILY-SUMMED PA in by_pitch_type for the opposing pitcher''s primary pitch family. Sum across canonical labels belonging to the same family (e.g. 4SM + SI + CT for fastball).'),

  -- ── Eligibility · Gate B (matchup exception) ────────────────────────────
  ('eligibility_gate_b_bvp_ab_min',          8,     NULL, NULL, 'count',
     'Phase 1 Gate B: minimum BvP at-bats vs today''s pitcher for the matchup history component to count.'),
  ('eligibility_gate_b_bvp_hr_min',          2,     NULL, NULL, 'count',
     'Phase 1 Gate B: BvP HR count that alone clears the matchup component (no avg requirement).'),
  ('eligibility_gate_b_bvp_avg_min',         0.300, NULL, NULL, 'decimal',
     'Phase 1 Gate B: BvP batting average threshold paired with the hr_alt_min HR count.'),
  ('eligibility_gate_b_bvp_hr_alt_min',      1,     NULL, NULL, 'count',
     'Phase 1 Gate B: HR count required alongside the BvP avg threshold (avg >= bvp_avg_min AND hr >= this).'),
  ('eligibility_gate_b_barrel_pct_min',      8.0,   NULL, NULL, 'percent',
     'Phase 1 Gate B: season barrel% floor (one of two power floors; OR with xSLG).'),
  ('eligibility_gate_b_xslg_min',            0.430, NULL, NULL, 'decimal',
     'Phase 1 Gate B: season xSLG floor (alternate power floor; OR with barrel%).'),

  -- ── Primary signal weights ──────────────────────────────────────────────
  ('primary_bvp_ab_min',                     8,     NULL, NULL, 'count',
     'Phase 1 primary signal: minimum BvP AB for the observed_vs_pitcher component (bvp.hr/bvp.ab) to enter the max.'),
  ('primary_pitch_type_pa_min',              20,    NULL, NULL, 'count',
     'Phase 1 primary signal: minimum FAMILY-SUMMED by_pitch_type PA (reliability gate) for the observed_vs_pitch_type component. The hr_pct used is the SPECIFIC primary pitch''s — family-summed PA only gates reliability.'),
  ('primary_l7_pa_min',                      10,    NULL, NULL, 'count',
     'Phase 1 primary signal: minimum L7 PA for the recent_power_form component. Below this we fall back to L14 xSLG with the same divisor.'),
  ('primary_l7_xslg_divisor',                2.0,   NULL, NULL, 'coef',
     'Phase 1 primary signal: L7 xSLG (or L14 fallback) is divided by this to scale into ~[0,1] signal space.'),

  -- ── EV demote thresholds ────────────────────────────────────────────────
  ('ev_edge_full_floor',                     0.03,  NULL, NULL, 'decimal',
     'Phase 1 EV screen: edge >= this → "full" (no stake-tier demote).'),
  ('ev_edge_drop_floor',                     0.0,   NULL, NULL, 'decimal',
     'Phase 1 EV screen: edge >= this AND < full_floor → "drop" (suggested_stake_tier bumped one step worse).'),
  ('ev_edge_warn_floor',                    -0.10,  NULL, NULL, 'decimal',
     'Phase 1 EV screen: edge >= this AND < drop_floor → "warn_drop" (drop + warning flag). Below this → "disqualify" (candidate dropped from the pool).'),

  -- ── Advisory stake-tier breakpoints (display-only) ──────────────────────
  ('stake_tier_lock_min',                    0.65,  NULL, NULL, 'decimal',
     'Phase 1 stake tier: primary_signal >= this → "lock" (display-only label; not applied to stake yet).'),
  ('stake_tier_safe_min',                    0.50,  NULL, NULL, 'decimal',
     'Phase 1 stake tier: primary_signal >= this → "safe".'),
  ('stake_tier_risky_min',                   0.30,  NULL, NULL, 'decimal',
     'Phase 1 stake tier: primary_signal >= this → "risky".'),
  ('stake_tier_lottery_min',                 0.15,  NULL, NULL, 'decimal',
     'Phase 1 stake tier: primary_signal >= this → "lottery"; below → "donation".')

ON CONFLICT (key) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════
-- END 28_phase1_matchup_first.sql
-- ════════════════════════════════════════════════════════════════════════
