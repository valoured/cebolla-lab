-- ────────────────────────────────────────────────────────────────
-- Migration 12: Add contact + combined-score snapshots to pods.
--
-- Captures the contact score (and the multiplicative combined score
-- of normalized edge × normalized contact) at lock-time so the POD
-- card can show "we picked this because of X edge AND Y contact"
-- on the public scoreboard.
--
-- Backfill behavior: existing POD rows get NULL (no contact score
-- was computed when they were locked). That's correct — we can't
-- retroactively know what L14 contact was on that date with the
-- exact pool composition.
-- ────────────────────────────────────────────────────────────────

ALTER TABLE pods
  ADD COLUMN IF NOT EXISTS contact_score   NUMERIC(5, 2),
  ADD COLUMN IF NOT EXISTS combined_score  NUMERIC(7, 1);

COMMENT ON COLUMN pods.contact_score IS
  'L14 contact score (0-100) at lock time. Percentile rank vs all qualified MLB batters across Brl%/HH%/xSLG.';

COMMENT ON COLUMN pods.combined_score IS
  'Normalized edge × normalized contact (0-10000) at lock time. The metric used to pick this POD.';
