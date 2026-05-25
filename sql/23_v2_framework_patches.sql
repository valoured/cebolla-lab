-- ════════════════════════════════════════════════════════════════════════
-- 23_v2_framework_patches.sql  ·  v2 picking-framework schema
-- ════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Schema support for the v2 pick_pod / pick_cards rewrite. Five pieces:
--     · Patch 9  — continuous confidence_score + tier-letter (pods/card_legs/cards)
--     · Patch 1  — batter_stats: season_hr_pace, barrel_rank_mlb
--     · Patch 4  — batter_stats: career_barrel_pct, pa_vs_pitch
--     · Patch 6  — near_miss_events table (ingestion stubbed; boost reader live)
--     · Patch 8  — model_calibration_alerts + version_history (auto-rollback)
--     · Config   — model_thresholds table, seeded from tier_system.py constants
--
-- TABLE-NAME TRANSLATION (spec → real production schema, applied throughout):
--     pod_picks  → pods        pick_cards → cards
--     per-leg    → card_legs    batters_dim → batter_stats
--
-- UNIT POLICY (decision: keep existing storage, adjust thresholds to match):
--     *_pct columns (barrel_pct, hard_hit_pct, hr_pct, career_barrel_pct) are
--     stored 0-100 (PERCENT). xSLG/xBA/hit_per_pa are 0-1 (DECIMAL). Thresholds
--     in model_thresholds follow the same convention (see `unit` column).
--
-- COLUMN WITHOUT A CLEAN HOME (flagged):
--     The spec's catcher exclusion (`candidate.position !== 'C'`) does NOT get
--     a new column here. `players.position` ALREADY exists and is populated
--     ('C' for catchers, e.g. Bo Naylor) — the pickers already join players,
--     so the v2 code reads players.position directly. Adding `position` to the
--     per-window batter_stats table would be redundant + denormalized.
--
-- TYPE NOTES vs raw spec text:
--     · near_miss_events.batter_id/pitcher_id/game_id are INTEGER FKs (the spec
--       wrote TEXT) because players.id and games.id are SERIAL/INTEGER here.
--     · The spec's inline `INDEX idx_batter_date (...)` is MySQL syntax —
--       rewritten as a separate Postgres CREATE INDEX below.
--     · gen_random_uuid() is core in Supabase's Postgres (no extension needed).
--
-- IDEMPOTENCY
--   ADD COLUMN / CREATE TABLE / CREATE INDEX all use IF NOT EXISTS. Seed
--   INSERTs use ON CONFLICT DO NOTHING so re-runs never clobber live-tuned
--   values in model_thresholds or version_history. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════


-- ════════════════════════════════════════════════════════════════════════
-- PATCH 9 · Continuous confidence score + tier letter
-- ════════════════════════════════════════════════════════════════════════
--   confidence_score is the v2 RANKING signal. It COEXISTS with the v1
--   combined_score and tier_score columns (decision #4): v1 picks already in
--   the tables keep their old scores; v2 picks populate confidence_score and
--   rank on it. Nothing is dropped or replaced.
--
--   Score is clamped 0.000-1.000 (NUMERIC(4,3); CHECK enforces the clamp).
--   Tier-letter mapping (computed in pick code, stored here for display/filter):
--     >= 0.75 'A+' | >= 0.65 'A' | >= 0.55 'A-' | >= 0.45 'B+'
--     >= 0.35 'B'  | >= 0.25 'C+' | else 'C'

-- ──── PODs ────────────────────────────────────────────────────────────────
ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(4, 3)
    CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)),
  ADD COLUMN IF NOT EXISTS confidence_tier  TEXT
    CHECK (confidence_tier IS NULL OR confidence_tier IN
           ('A+','A','A-','B+','B','C+','C'));

COMMENT ON COLUMN pods.confidence_score IS
  'v2 continuous confidence [0,1], the v2 ranking signal. Coexists with v1 combined_score/tier_score (not a replacement). NULL on v1-era picks.';
COMMENT ON COLUMN pods.confidence_tier IS
  'v2 letter grade derived from confidence_score. Breakpoints: 0.75 A+ / 0.65 A / 0.55 A- / 0.45 B+ / 0.35 B / 0.25 C+ / else C.';

-- ──── Card legs ───────────────────────────────────────────────────────────
ALTER TABLE card_legs
  ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(4, 3)
    CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)),
  ADD COLUMN IF NOT EXISTS confidence_tier  TEXT
    CHECK (confidence_tier IS NULL OR confidence_tier IN
           ('A+','A','A-','B+','B','C+','C'));

COMMENT ON COLUMN card_legs.confidence_score IS 'See pods.confidence_score — per-leg v2 confidence.';

-- ──── Cards (aggregate of leg confidence) ─────────────────────────────────
--   cards.confidence_score = aggregate (avg / weighted-avg, picker's choice) of
--   its legs' confidence_scores, used for card-level ranking in pick_cards.py.
--   NOTE: the EXISTING cards.tier column (TEXT leg-count enum:
--   straight/two_leg/three_leg/four_leg) is deliberately LEFT UNTOUCHED — v1
--   and the production frontend depend on it (decision #3). The letter grade
--   goes in the new confidence_tier column.
ALTER TABLE cards
  ADD COLUMN IF NOT EXISTS confidence_score NUMERIC(4, 3)
    CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 1)),
  ADD COLUMN IF NOT EXISTS confidence_tier  TEXT
    CHECK (confidence_tier IS NULL OR confidence_tier IN
           ('A+','A','A-','B+','B','C+','C'));

COMMENT ON COLUMN cards.confidence_score IS
  'Card-level aggregate of leg confidence_scores (avg or weighted-avg). Card ranking signal in pick_cards.py.';
COMMENT ON COLUMN cards.confidence_tier IS
  'Card letter grade from confidence_score (same breakpoints as pods.confidence_tier). Separate from cards.tier (leg-count category — untouched).';

-- Ranking indices (mirror the existing tier_score indices). card_legs is not
-- independently ranked, so no leg-level confidence index.
CREATE INDEX IF NOT EXISTS idx_pods_confidence_score
  ON pods(confidence_score DESC)
  WHERE confidence_score IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cards_confidence_score
  ON cards(confidence_score DESC)
  WHERE confidence_score IS NOT NULL;


-- ════════════════════════════════════════════════════════════════════════
-- PATCH 1 & 4 · New batter_stats inputs
-- ════════════════════════════════════════════════════════════════════════
ALTER TABLE batter_stats
  -- Patch 1: projected season HR COUNT (not a rate). Spec gate example:
  --          candidate.season_hr_pace >= 15  → an integer count of HRs.
  ADD COLUMN IF NOT EXISTS season_hr_pace    INTEGER,
  -- Patch 1: 1-based rank of this batter's barrel% among qualified MLB hitters.
  ADD COLUMN IF NOT EXISTS barrel_rank_mlb   INTEGER,
  -- Patch 4: career barrel% — PERCENT (0-100), same unit as barrel_pct.
  ADD COLUMN IF NOT EXISTS career_barrel_pct NUMERIC(5, 2),
  -- Patch 4: per-pitch-type PA / performance splits, structured.
  ADD COLUMN IF NOT EXISTS pa_vs_pitch       JSONB;

COMMENT ON COLUMN batter_stats.season_hr_pace IS
  'Projected full-season HR COUNT (integer), e.g. 25. NOT a rate. Patch 1 gate compares as a count (>= 15).';
COMMENT ON COLUMN batter_stats.barrel_rank_mlb IS
  'Patch 1: 1-based MLB rank of barrel% among qualified hitters (1 = best).';
COMMENT ON COLUMN batter_stats.career_barrel_pct IS
  'Patch 4: career barrel%, stored as PERCENT (0-100) to match barrel_pct units.';
COMMENT ON COLUMN batter_stats.pa_vs_pitch IS
  'Patch 4: JSONB of plate-appearance splits vs pitch type. Shape mirrors by_pitch_type but holds PA-level matchup detail used by the v2 primary-signal calc.';


-- ════════════════════════════════════════════════════════════════════════
-- PATCH 6 · near_miss_events  (ingestion STUBBED; getNearMissBoost reads this)
-- ════════════════════════════════════════════════════════════════════════
--   A "near miss" = a batted ball that was nearly a HR (hard contact that
--   stayed in the park / would-be HR in N parks). Populated later by
--   near_miss_ingestion.py (Statcast / MLBNearHR feed — stub this session).
--
--   getNearMissBoost() reads:
--     WHERE batter_id = ? AND game_date >= now() - INTERVAL '5 days'
--       AND exit_velocity_mph >= 100
--     → 2+ matching rows ⇒ boost 0.05, else 0.
--   (Those params live in model_thresholds: near_miss_* keys below.)
CREATE TABLE IF NOT EXISTS near_miss_events (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  batter_id          INTEGER NOT NULL REFERENCES players(id),  -- spec TEXT → real INTEGER FK
  pitcher_id         INTEGER REFERENCES players(id),
  game_id            INTEGER REFERENCES games(id),
  game_date          DATE NOT NULL,
  pitch_type         TEXT,
  exit_velocity_mph  NUMERIC(5, 2),   -- spec DECIMAL; e.g. 104.30
  launch_angle_deg   NUMERIC(5, 2),   -- spec DECIMAL; may be negative
  distance_ft        INTEGER,
  ballpark           TEXT,
  hr_in_n_parks      INTEGER,         -- "would have been a HR in N of 30 parks"
  result_text        TEXT,            -- raw description of the outcome
  created_at         TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE near_miss_events IS
  'Patch 6: would-be-HR batted balls. Ingestion is stubbed (near_miss_ingestion.py TODO); getNearMissBoost() reads it for a recent-hard-contact boost.';

-- Serves the boost query (batter_id equality + recent-date range). EV filter is
-- applied as a residual predicate — per-batter 5-day slices are tiny.
CREATE INDEX IF NOT EXISTS idx_near_miss_batter_date
  ON near_miss_events(batter_id, game_date DESC);


-- ════════════════════════════════════════════════════════════════════════
-- PATCH 8 · model_calibration_alerts + version_history (auto-rollback)
-- ════════════════════════════════════════════════════════════════════════
--   model_calibration_alerts: renamed from spec's `calibration_alerts` to avoid
--   confusion with migration 20 (which is CLV-scoped, not model calibration).
--   The Python module stays calibration.py (unambiguous in code context).
CREATE TABLE IF NOT EXISTS model_calibration_alerts (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  alert_date         DATE DEFAULT CURRENT_DATE,
  severity           TEXT CHECK (severity IN ('low','medium','high','critical')),
  issue              TEXT NOT NULL,
  rate               NUMERIC,           -- spec DECIMAL: the observed/expected rate that tripped the alert
  action_recommended TEXT,
  action_taken       TEXT,
  resolved           BOOLEAN DEFAULT FALSE,
  created_at         TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE model_calibration_alerts IS
  'Patch 8: model-calibration alerts (distinct from migration 20 CLV). Drives the auto-rollback decision when severity=critical.';

-- Unresolved-alerts dashboard / rollback check.
CREATE INDEX IF NOT EXISTS idx_model_calib_alerts_open
  ON model_calibration_alerts(alert_date DESC)
  WHERE resolved = FALSE;

--   version_history: which framework version is live, with the config snapshot
--   and rollback metadata. Patch 8's auto-rollback flips `active` back to the
--   prior tag (v1-final-2026-05-25) on a critical alert.
CREATE TABLE IF NOT EXISTS version_history (
  version_tag     TEXT PRIMARY KEY,
  active          BOOLEAN DEFAULT FALSE,
  config          JSONB,                 -- threshold snapshot for this version (nullable)
  promoted_at     TIMESTAMPTZ DEFAULT NOW(),
  rollback_reason TEXT
);

COMMENT ON TABLE version_history IS
  'Patch 8: framework version registry. Exactly one row should have active=TRUE. Auto-rollback re-activates the prior tag and records rollback_reason.';

-- Partial index for the "current active version" lookup (expected 1 row).
CREATE INDEX IF NOT EXISTS idx_version_history_active
  ON version_history(active)
  WHERE active = TRUE;

-- Seed the two framework versions.
--   v1-final-2026-05-25 (inactive): config is a LITERAL SNAPSHOT of the
--     tier_system.py constants v1 ran on, so Patch 8's kill switch knows the
--     exact state to restore on rollback. Matches the git tag pushed pre-v2.
--     NOTE: deliberately contains ONLY v1 thresholds — none of the v2 NEW
--     params (near-miss, confidence, catcher, stack, wind/temp), which v1
--     never had.
--   v2-launch-2026-05-26 (active): config left NULL for now — calibration.py
--     snapshots model_thresholds into it at promotion time.
INSERT INTO version_history (version_tag, active, config, rollback_reason) VALUES
  ('v1-final-2026-05-25', FALSE,
   '{
      "_source": "tier_system.py constants as of v1-final-2026-05-25 (pre-v2)",
      "t1_hr_barrel_min": 12.0,
      "t1_hr_xslg_min": 0.600,
      "t1_hr_hrvspitch_min": 8.0,
      "t1_hrr_hitrate_min": 0.30,
      "t1_hrr_xba_min": 0.280,
      "t1_hrr_bvp_ba_min": 0.280,
      "t1_hrr_bvp_pa_min": 8,
      "t2_heat_tiers_qualifying": ["HOT", "BLAZING"],
      "t2_hh_pct_min": 45.0,
      "t2_contact_min": 70.0,
      "t2_bvp_ba_min": 0.280,
      "t2_bvp_pa_min": 8,
      "score_base_triple": 1.10,
      "score_base_standard": 1.00,
      "score_base_stowers": 0.85,
      "tier2_bonus_per_hit": 0.05,
      "tier2_bonus_cap": 0.15,
      "stowers_tier2_required": 3,
      "stake_mod_floor": 0.7,
      "stake_mod_ceil": 1.3
    }'::jsonb,
   NULL),
  ('v2-launch-2026-05-26', TRUE, NULL, NULL)
ON CONFLICT (version_tag) DO NOTHING;


-- ════════════════════════════════════════════════════════════════════════
-- CONFIG · model_thresholds (no hardcoded thresholds)
-- ════════════════════════════════════════════════════════════════════════
--   Key/value table seeded from scripts/tier_system.py constants (the v1 gates,
--   already in the correct units) PLUS the new v2 scalar thresholds introduced
--   by Patches 6 and 9. The pickers load this ONCE per run into an in-process
--   dict and do in-memory lookups (no per-candidate queries).
--
--   `unit`: 'percent' (0-100) | 'decimal' (0-1) | 'count' | 'score' (0-100) |
--           'mph' | 'set' (comma-joined text_value) | 'days'.
--   `market_class`: 'hr' | 'hrr' | NULL (applies to both / global).
CREATE TABLE IF NOT EXISTS model_thresholds (
  key          TEXT PRIMARY KEY,
  num_value    NUMERIC,          -- numeric thresholds (counts stored here too)
  text_value   TEXT,             -- non-numeric values (e.g. qualifying heat-tier set)
  market_class TEXT,             -- 'hr' | 'hrr' | NULL
  unit         TEXT,             -- interpretation hint (see header)
  description  TEXT,             -- plain-English meaning (read this, not the code)
  updated_at   TIMESTAMPTZ DEFAULT NOW()
);

COMMENT ON TABLE model_thresholds IS
  'Single source of truth for picker thresholds. Loaded once per pick run and cached in-process. Seeded from tier_system.py; editable live (re-running migration 23 will NOT overwrite tuned values).';

INSERT INTO model_thresholds (key, num_value, text_value, market_class, unit, description) VALUES
  -- ── Tier 1 · HR market gates ──
  ('t1_hr_barrel_min',     12.0,  NULL, 'hr',  'percent',
     'Tier 1 gate A (HR): min barrel% over last 14 days for the barrel gate to pass.'),
  ('t1_hr_xslg_min',       0.600, NULL, 'hr',  'decimal',
     'Tier 1 gate B (HR): min expected slugging (xSLG, L14).'),
  ('t1_hr_hrvspitch_min',  8.0,   NULL, 'hr',  'percent',
     'Tier 1 gate C (HR): min HR rate (%) vs the pitcher''s primary pitch type (season by_pitch_type).'),
  -- ── Tier 1 · HRR (1+ hits) market gates ──
  ('t1_hrr_hitrate_min',   0.30,  NULL, 'hrr', 'decimal',
     'Tier 1 gate A (HRR): min hit rate (hit_per_pa, L14).'),
  ('t1_hrr_xba_min',       0.280, NULL, 'hrr', 'decimal',
     'Tier 1 gate B (HRR): min expected batting average (xBA, L14).'),
  ('t1_hrr_bvp_ba_min',    0.280, NULL, 'hrr', 'decimal',
     'Tier 1 gate C (HRR): min career batting average vs this pitcher (BvP).'),
  ('t1_hrr_bvp_pa_min',    8,     NULL, 'hrr', 'count',
     'Tier 1 gate C (HRR): min plate appearances for the BvP sample to count.'),
  -- ── Tier 2 · confirming signals (apply to both markets) ──
  ('t2_heat_tiers_qualifying', NULL, 'HOT,BLAZING', NULL, 'set',
     'Tier 2 signal A: batter_trends.combined_tier values that count as "hot enough".'),
  ('t2_hh_pct_min',        45.0,  NULL, NULL,  'percent',
     'Tier 2 signal B: min hard-hit% (L14).'),
  ('t2_contact_min',       70.0,  NULL, NULL,  'score',
     'Tier 2 signal C: min contact composite score (0-100).'),
  ('t2_bvp_ba_min',        0.280, NULL, NULL,  'decimal',
     'Tier 2 signal D: min BvP career BA (gives BvP double weight in HRR).'),
  ('t2_bvp_pa_min',        8,     NULL, NULL,  'count',
     'Tier 2 signal D: min BvP plate appearances.'),
  -- ── Scoring (qualification path base scores + Tier 2 bonus) ──
  ('score_base_triple',    1.10,  NULL, NULL,  'score',
     'Base tier_score for 3-of-3 Tier 1 (full triple).'),
  ('score_base_standard',  1.00,  NULL, NULL,  'score',
     'Base tier_score for 2-of-3 Tier 1 (standard qualifier).'),
  ('score_base_stowers',   0.85,  NULL, NULL,  'score',
     'Base tier_score for the Stowers rule (1-of-3 Tier 1 + 3+ Tier 2).'),
  ('tier2_bonus_per_hit',  0.05,  NULL, NULL,  'score',
     'Added to base score per Tier 2 hit.'),
  ('tier2_bonus_cap',      0.15,  NULL, NULL,  'score',
     'Maximum total Tier 2 bonus.'),
  ('stowers_tier2_required', 3,   NULL, NULL,  'count',
     'Tier 2 hits required to qualify a 1-of-3 Tier 1 pick via the Stowers rule.'),
  -- ── Tier 4 · stake modifier clamps ──
  ('stake_mod_floor',      0.7,   NULL, NULL,  'decimal',
     'Lower clamp on the park×weather informational stake modifier.'),
  ('stake_mod_ceil',       1.3,   NULL, NULL,  'decimal',
     'Upper clamp on the park×weather informational stake modifier.'),
  -- ── Patch 6 · near-miss boost params (v2 NEW) ──
  ('near_miss_ev_floor_mph', 100,  NULL, NULL, 'mph',
     'Patch 6: min exit velocity (mph) for a near-miss event to count toward the boost.'),
  ('near_miss_lookback_days', 5,   NULL, NULL, 'days',
     'Patch 6: trailing window (days) for counting near-miss events.'),
  ('near_miss_min_events', 2,     NULL, NULL,  'count',
     'Patch 6: number of qualifying near-miss events that triggers the boost.'),
  ('near_miss_boost',      0.05,  NULL, NULL,  'decimal',
     'Patch 6: confidence boost added when near_miss_min_events is met.'),
  -- ── Patch 9 · confidence → letter breakpoints (v2 NEW) ──
  ('confidence_tier_a_plus',  0.75, NULL, NULL, 'decimal', 'Patch 9: confidence_score >= this → A+.'),
  ('confidence_tier_a',       0.65, NULL, NULL, 'decimal', 'Patch 9: confidence_score >= this → A.'),
  ('confidence_tier_a_minus', 0.55, NULL, NULL, 'decimal', 'Patch 9: confidence_score >= this → A-.'),
  ('confidence_tier_b_plus',  0.45, NULL, NULL, 'decimal', 'Patch 9: confidence_score >= this → B+.'),
  ('confidence_tier_b',       0.35, NULL, NULL, 'decimal', 'Patch 9: confidence_score >= this → B.'),
  ('confidence_tier_c_plus',  0.25, NULL, NULL, 'decimal', 'Patch 9: confidence_score >= this → C+ (else C).'),
  -- ── Patch 1 · catcher promotion gates (v2 NEW) ──
  -- Catchers are excluded by default (players.position = 'C'); these let an
  -- elite-power catcher earn back into HR consideration.
  ('catcher_promote_hr_pace_min',    15,    NULL, 'hr', 'count',
     'Patch 1: a catcher is promoted back into HR consideration only if projected season HR pace (count) >= this.'),
  ('catcher_promote_barrel_rank_max', 50,   NULL, 'hr', 'count',
     'Patch 1: catcher promotion requires MLB barrel% rank <= this (i.e. top-50; lower rank = better).'),
  ('catcher_promote_xslg_l14_min',   0.450, NULL, 'hr', 'decimal',
     'Patch 1: catcher promotion requires xSLG (L14) >= this.'),
  -- ── Patch 5 · vulnerable-stack detection (v2 NEW, pick_cards) ──
  ('stack_min_candidates', 3,    NULL, NULL, 'count',
     'Patch 5: min qualifying batters vs one pitcher to form a vulnerable-stack play.'),
  ('stack_xfip_min',       4.25, NULL, NULL, 'rate',
     'Patch 5: opposing pitcher xFIP >= this marks the matchup as stackable (higher = more vulnerable).'),
  ('stack_hr9_min',        1.3,  NULL, NULL, 'rate',
     'Patch 5: opposing pitcher HR/9 >= this marks the matchup as stackable.'),
  ('stack_boost',          0.10, NULL, NULL, 'decimal',
     'Patch 5: confidence boost applied to legs that belong to a detected vulnerable stack.'),
  -- ── Patch 2 · game-environment wind/temp/humidity/elevation (v2 NEW) ──
  ('wind_out_coef_per_mph', 0.015, NULL, NULL, 'coef',
     'Patch 2: HR-environment multiplier INCREMENT per mph of wind blowing OUT to the field.'),
  ('wind_in_coef_per_mph',  0.018, NULL, NULL, 'coef',
     'Patch 2: HR-environment multiplier DECREMENT per mph of wind blowing IN.'),
  ('temp_coef_per_deg_f',   0.01,  NULL, NULL, 'coef',
     'Patch 2: HR-environment multiplier increment per degree F above the reference temperature.'),
  ('humidity_threshold_pct', 70,   NULL, NULL, 'percent',
     'Patch 2: relative humidity (%) above which the humidity boost applies.'),
  ('humidity_boost',        1.02,  NULL, NULL, 'multiplier',
     'Patch 2: HR-environment multiplier applied when humidity exceeds humidity_threshold_pct.'),
  ('elevation_threshold_ft', 4000, NULL, NULL, 'feet',
     'Patch 2: park elevation (ft) above which the elevation boost applies (Coors-type parks).'),
  ('elevation_boost',       1.05,  NULL, NULL, 'multiplier',
     'Patch 2: HR-environment multiplier applied when elevation exceeds elevation_threshold_ft.')
ON CONFLICT (key) DO NOTHING;

-- ════════════════════════════════════════════════════════════════════════
-- END 23_v2_framework_patches.sql
-- ════════════════════════════════════════════════════════════════════════
