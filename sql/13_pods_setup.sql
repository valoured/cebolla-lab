-- ────────────────────────────────────────────────────────────────
-- Play of the Day (POD) system — combined migration.
--
-- Originally split across 10_pods.sql + 12_pods_contact.sql, but since
-- the table was never created, this single file does both jobs cleanly.
-- Safe to run on a fresh DB or one that already has the table — uses
-- IF NOT EXISTS / ADD COLUMN IF NOT EXISTS throughout.
--
-- Every day, an automated pick. One row per slate. The system:
--   1. Picks the single best HR prop using a multiplicative blend of
--      normalized edge × normalized contact score, gated to picks where
--      projected_prob >= 0.30
--   2. Locks it in before games start (status='pending', locked_at=now)
--   3. After games finish, a settle script flips status to win/loss
--      based on whether the player hit a HR
--
-- The page on the frontend reads this table to show:
--   - Today's POD (if any)
--   - Running cumulative P&L across all settled PODs
--   - Recent picks history with W/L marks
--
-- Stake is canonical $10 — viewer can scale the displayed P&L in the UI
-- but the underlying record stays at $10 for honesty.
-- ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS pods (
    id                  SERIAL PRIMARY KEY,
    -- Slate date this POD belongs to (ET-relative baseball day)
    pod_date            DATE NOT NULL UNIQUE,
    -- Pick details (snapshot at lock time — these don't change after settle)
    game_id             INTEGER REFERENCES games(id),
    player_id           INTEGER REFERENCES players(id),
    player_mlbam_id     INTEGER,                        -- snapshot for headshot URL (denormalized)
    market              TEXT NOT NULL,                  -- 'hr_anytime' for now
    projected_prob      NUMERIC(6, 4) NOT NULL,         -- Cebolla's win prob at lock
    no_vig_prob         NUMERIC(6, 4),                  -- market's implied prob
    edge                NUMERIC(6, 4),                  -- projected_prob - no_vig_prob
    american_odds       INTEGER NOT NULL,               -- odds at lock time
    book                TEXT,                           -- which book the odds came from
    model_version       TEXT,                           -- which model picked this
    -- Snapshot of player/team context for display (avoids joins)
    player_name         TEXT NOT NULL,
    team_abbrev         TEXT,
    opponent_abbrev     TEXT,
    -- Contact + combined-score snapshot at lock time (NULL for picks made
    -- before the contact-score feature shipped, including any retroactive
    -- backfills).
    contact_score       NUMERIC(5, 2),
    combined_score      NUMERIC(7, 1),
    -- Stake/payout (canonical $10 stake)
    stake               NUMERIC(8, 2) DEFAULT 10.00,
    -- After settling
    status              TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'win' | 'loss' | 'push' | 'void'
    payout              NUMERIC(10, 2),                 -- realized P&L (NULL until settled). +90 for win at +900, -10 for loss
    settled_at          TIMESTAMPTZ,
    -- Timestamps
    locked_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- If the table already existed without the new columns, add them safely:
ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS contact_score   NUMERIC(5, 2),
  ADD COLUMN IF NOT EXISTS combined_score  NUMERIC(7, 1);

-- One POD per day, enforced by UNIQUE(pod_date).
-- Idempotency for the pick script: ON CONFLICT(pod_date) DO NOTHING.

CREATE INDEX IF NOT EXISTS idx_pods_status      ON pods(status);
CREATE INDEX IF NOT EXISTS idx_pods_date_desc   ON pods(pod_date DESC);

COMMENT ON TABLE pods IS
  'Cebolla Lab Play of the Day — one auto-picked HR prop per slate, '
  'logged at lock time and settled after games complete. Drives the '
  'public POD scoreboard on the frontend.';

COMMENT ON COLUMN pods.contact_score IS
  'L14 contact score (0-100) at lock time. Percentile rank vs all qualified MLB batters across Brl%/HH%/xSLG.';

COMMENT ON COLUMN pods.combined_score IS
  'Normalized edge × normalized contact (0-10000) at lock time. The metric used to pick this POD.';

COMMENT ON COLUMN pods.payout IS
  'Realized P&L in dollars at the canonical $10 stake. Win = stake * '
  '(odds/100 if positive, 100/abs(odds) if negative). Loss = -stake. '
  'Push = 0. NULL until settled.';
