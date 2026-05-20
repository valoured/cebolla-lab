-- ────────────────────────────────────────────────────────────────
-- Migration 17: Cebolla Cards — AI-built daily parlays.
--
-- Cards are model-generated betting combinations beyond the POD. Each card
-- is a parlay of N legs (2, 3, or 4) where each leg is a single prop pick.
-- Cards are picked daily at the same 2:45 AM ET lock window as PODs and
-- settled post-game alongside settle_pods.
--
-- Tiers:
--   straight  → single-leg (same as a POD, kept here for unified history)
--   two_leg   → 2 legs
--   three_leg → 3 legs
--   four_leg  → 4-leg lottery card
--
-- Recommended stakes by tier (canonical — frontend can scale):
--   straight  → $10
--   two_leg   → $10
--   three_leg → $5
--   four_leg  → $1 (lottery)
-- ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS cards (
    id              SERIAL PRIMARY KEY,
    card_date       DATE NOT NULL,
    tier            TEXT NOT NULL
                    CHECK (tier IN ('straight', 'two_leg', 'three_leg', 'four_leg')),
    label           TEXT,                -- e.g. "Power Stack", "Lottery Shot"
    leg_count       INT NOT NULL,

    -- Parlay math
    combined_prob   NUMERIC(6, 5),       -- product of leg probs (with correlation adjustment)
    combined_odds   INT,                 -- American odds of the parlay
    decimal_odds    NUMERIC(8, 3),       -- parlay decimal odds
    implied_prob    NUMERIC(6, 5),       -- implied from combined_odds
    edge            NUMERIC(6, 5),       -- combined_prob - implied_prob
    ev_per_dollar   NUMERIC(6, 4),       -- EV per $1 stake (e.g. 0.15 = 15c profit per $1)

    -- Stake + payout
    stake_rec       NUMERIC(6, 2) NOT NULL DEFAULT 10.00,
    payout_if_hit   NUMERIC(8, 2),       -- profit (not total return) at stake_rec

    -- Settlement
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'win', 'loss', 'void')),
    payout          NUMERIC(8, 2),       -- actual P&L at stake_rec (signed)
    settled_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cards_date ON cards(card_date DESC);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
CREATE INDEX IF NOT EXISTS idx_cards_tier ON cards(tier);

-- ────────────────────────────────────────────────────────────────
-- card_legs — one row per leg on a card. Order matters (leg_order).
-- ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS card_legs (
    id              SERIAL PRIMARY KEY,
    card_id         INT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    leg_order       INT NOT NULL,        -- 1-indexed display order

    -- Game + player context
    game_id         INT REFERENCES games(id),
    player_id       INT REFERENCES players(id),
    player_mlbam_id INT,                 -- denormalized for headshot URLs
    player_name     TEXT,
    team_abbrev     TEXT,
    opponent_abbrev TEXT,

    -- Market
    market          TEXT NOT NULL,       -- 'hr_anytime' | 'h_r_rbi_1.5' | 'hits_yes' | 'rbi_yes' etc
    line            NUMERIC(4, 1),       -- 0.5 / 1.5 / 2.5 / 3.5
    projected_prob  NUMERIC(6, 5),
    no_vig_prob     NUMERIC(6, 5),
    american_odds   INT,
    edge            NUMERIC(6, 5),
    book            TEXT DEFAULT 'draftkings',

    -- Per-leg settlement (a leg can be win/loss/void independently)
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'win', 'loss', 'void')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(card_id, leg_order)
);

CREATE INDEX IF NOT EXISTS idx_card_legs_card ON card_legs(card_id);
CREATE INDEX IF NOT EXISTS idx_card_legs_game ON card_legs(game_id);
CREATE INDEX IF NOT EXISTS idx_card_legs_player ON card_legs(player_id);

-- Documentation
COMMENT ON TABLE cards IS
  'Cebolla Cards — AI-built daily parlays. One row per card. '
  'Tier determines leg count and stake recommendation. '
  'Status = pending until all legs settle. Win requires ALL legs to win. '
  'Loss = any leg loses. Void = all remaining legs void after some won.';

COMMENT ON TABLE card_legs IS
  'Individual legs on a Cebolla Card. Each leg is a single prop pick. '
  'Per-leg status lets the frontend show partial progress (green/red dots) '
  'before the full card settles.';
