/**
 * useContactScore.js — Pure functions for computing a single-number
 * "Contact Score" per batter, plus a trend indicator vs the season baseline.
 *
 * Design summary:
 *
 *   For each batter on tonight's slate (qualified = L14 PA >= MIN_PA),
 *   compute a 0-100 percentile rank for three metrics within the tonight-slate
 *   comparison pool:
 *
 *     - barrel_pct      (weight 0.40)
 *     - hard_hit_pct    (weight 0.30)
 *     - xslg            (weight 0.30)
 *
 *   Weighted sum of those percentile ranks IS the contact score.
 *
 *   Trend = (L14 contact score) - (season contact score for the same batter,
 *   computed against the same percentile pool). A delta > +5 surfaces as
 *   "running hot," < -5 as "running cold," anything in between is no arrow.
 *
 * Why percentile rank instead of raw stat -> score?
 *   - The scale of Brl% (0-25%), HH% (0-60%), and xSLG (0-1.0) are radically
 *     different. Mixing raw values would weight whichever has the wider range.
 *   - Percentile rank within tonight's pool normalizes to "where does this
 *     batter sit among tonight's options," which is exactly what we want for
 *     card-building.
 *
 * Edge cases handled:
 *   - PA < MIN_PA: contact score is null. Sample too small for honest rate stats.
 *   - Any of Brl%/HH%/xSLG is null individually: that COMPONENT defaults to a
 *     neutral 50 percentile so we don't throw away the entire score just
 *     because one metric has insufficient data. Honest about partial data.
 *   - Empty/small pools (<3 qualified batters): percentile rank is meaningless,
 *     so the score returns null.
 *   - Identical values: percentile rank uses average rank tie-breaking, so
 *     5 batters all at exactly 8.5% Brl% all get the same percentile.
 */

// ── Constants ────────────────────────────────────────────────────────

/** Minimum L14 PA for a batter to qualify for contact scoring. 20 PA is the
 *  smallest sample where rate stats stop being random noise — below that, one
 *  fluky hit moves the percentage by 5+ points. */
export const MIN_PA = 20

/** Minimum pool size needed before percentile rank means anything. */
export const MIN_POOL_SIZE = 3

/** Component weights — must sum to 1.0. */
export const WEIGHTS = {
  barrel_pct:   0.40,  // most predictive single contact metric
  hard_hit_pct: 0.30,  // consistency of strong contact
  xslg:         0.30,  // expected slugging — translates to actual bases
}

/** Threshold (in score points) above which trend arrows are shown.
 *  Below this, the L14 vs season delta is treated as noise. */
export const TREND_THRESHOLD = 5

/** Neutral percentile assigned to a missing component. Halfway = no signal. */
const NEUTRAL_PERCENTILE = 50

// ── Helpers ──────────────────────────────────────────────────────────

/**
 * Compute the percentile rank of `value` within a pool of finite numbers.
 *
 * Uses the standard "average rank" definition:
 *   percentile = (count_below + 0.5 * count_equal) / count_total
 *
 * Returns 0-100. Returns null if:
 *   - value is null/NaN
 *   - pool has fewer than MIN_POOL_SIZE finite values
 *
 * @param {number|null} value
 * @param {number[]} pool — array of finite numbers to rank against
 * @returns {number|null} 0-100 percentile, or null if cannot compute
 */
export function percentileRank(value, pool) {
  if (value == null || !Number.isFinite(Number(value))) return null
  if (!Array.isArray(pool)) return null

  // Filter pool to finite numbers only (defense against accidental nulls)
  const finitePool = pool.filter(v => v != null && Number.isFinite(Number(v))).map(Number)
  if (finitePool.length < MIN_POOL_SIZE) return null

  const v = Number(value)
  let below = 0
  let equal = 0
  for (const p of finitePool) {
    if (p < v) below++
    else if (p === v) equal++
  }
  // Average rank: count_below + half of ties, divided by total
  const rank = (below + equal / 2) / finitePool.length
  return Math.max(0, Math.min(100, rank * 100))
}

/**
 * Build the percentile pools for each contact component from a list of stat
 * rows. Only includes rows that meet MIN_PA. Returns an object keyed by
 * component name, each value an array of finite numbers.
 *
 * @param {Array<{pa, barrel_pct, hard_hit_pct, xslg}>} statRows
 * @returns {{barrel_pct: number[], hard_hit_pct: number[], xslg: number[]}}
 */
export function buildContactPools(statRows) {
  const pools = {
    barrel_pct: [],
    hard_hit_pct: [],
    xslg: [],
  }
  if (!Array.isArray(statRows)) return pools

  for (const row of statRows) {
    if (!row) continue
    const pa = Number(row.pa)
    if (!Number.isFinite(pa) || pa < MIN_PA) continue

    for (const key of Object.keys(pools)) {
      const v = row[key]
      if (v != null && Number.isFinite(Number(v))) {
        pools[key].push(Number(v))
      }
    }
  }
  return pools
}

/**
 * Compute the contact score for a single stats row given pre-built pools.
 *
 * Returns:
 *   - null if PA < MIN_PA (insufficient sample, refuse to score)
 *   - null if all three components are missing (can't score from nothing)
 *   - 0-100 otherwise (weighted percentile composite)
 *
 * Missing components default to NEUTRAL_PERCENTILE (50) so a batter with a
 * Brl% and HH% but missing xSLG still gets a meaningful score weighted
 * toward what we DO know about them.
 *
 * @param {{pa, barrel_pct, hard_hit_pct, xslg}} stats
 * @param {{barrel_pct: number[], hard_hit_pct: number[], xslg: number[]}} pools
 * @returns {number|null}
 */
export function contactScore(stats, pools) {
  if (!stats) return null
  const pa = Number(stats.pa)
  if (!Number.isFinite(pa) || pa < MIN_PA) return null

  let weightedSum = 0
  let totalWeight = 0
  let hadAtLeastOneRealValue = false

  for (const [key, weight] of Object.entries(WEIGHTS)) {
    const v = stats[key]
    const pool = pools[key] || []
    let pct
    if (v != null && Number.isFinite(Number(v)) && pool.length >= MIN_POOL_SIZE) {
      pct = percentileRank(v, pool)
      hadAtLeastOneRealValue = true
    } else {
      pct = NEUTRAL_PERCENTILE
    }
    if (pct == null) pct = NEUTRAL_PERCENTILE
    weightedSum += pct * weight
    totalWeight += weight
  }

  if (!hadAtLeastOneRealValue) return null
  if (totalWeight === 0) return null

  return Math.round((weightedSum / totalWeight) * 10) / 10  // 1 decimal place
}

/**
 * Compute the trend (L14 vs season) for a batter.
 *
 * Returns:
 *   - null if either score is null (can't compute a delta from nothing)
 *   - The delta as a signed number, with 1 decimal place
 *
 * Caller decides what to do with it. The UI hides arrows when |delta| <
 * TREND_THRESHOLD because under that threshold the change is noise, not signal.
 *
 * @param {number|null} l14Score
 * @param {number|null} seasonScore
 * @returns {number|null}
 */
export function contactTrend(l14Score, seasonScore) {
  if (l14Score == null || seasonScore == null) return null
  return Math.round((l14Score - seasonScore) * 10) / 10
}

/**
 * Convenience: given a list of L14 stat rows and an aligned list of season
 * stat rows (same batters in same order), return contact score + trend for
 * each. Builds pools once from the L14 set so percentile rankings are
 * "vs tonight's slate" consistent with the project decision.
 *
 * Each input is an array of objects { batter_id, pa, barrel_pct, hard_hit_pct, xslg }.
 *
 * @returns {Map<number, {score: number|null, trend: number|null}>}
 *   keyed by batter_id
 */
export function buildContactSnapshot(l14Rows, seasonRows) {
  const out = new Map()
  if (!Array.isArray(l14Rows)) return out

  // Pool is built from L14 stats so the percentile baseline matches "tonight's slate."
  const pool = buildContactPools(l14Rows)
  const seasonById = new Map(
    (Array.isArray(seasonRows) ? seasonRows : [])
      .filter(r => r && r.batter_id != null)
      .map(r => [r.batter_id, r])
  )

  for (const row of l14Rows) {
    if (!row || row.batter_id == null) continue
    const l14Score = contactScore(row, pool)
    const seasonRow = seasonById.get(row.batter_id)
    // For trend, score the SAME batter's season stats against the SAME pool.
    // This keeps the comparison on the same scale.
    const seasonScore = seasonRow ? contactScore(seasonRow, pool) : null
    out.set(row.batter_id, {
      score: l14Score,
      trend: contactTrend(l14Score, seasonScore),
    })
  }
  return out
}

// ── Display helpers ──────────────────────────────────────────────────

/**
 * Format a score as a readable integer with optional trend marker.
 *
 * Examples:
 *   formatScore(78.4)           -> "78"
 *   formatScore(null)           -> "—"
 *
 * @param {number|null} score
 * @returns {string}
 */
export function formatScore(score) {
  if (score == null || !Number.isFinite(score)) return '—'
  return String(Math.round(score))
}

/**
 * Decide whether to show a trend arrow and what direction.
 * Returns:
 *   { show: false }
 *   { show: true, direction: 'up' | 'down', magnitude: integer }
 */
export function formatTrend(trend) {
  if (trend == null || !Number.isFinite(trend)) return { show: false }
  const mag = Math.abs(trend)
  if (mag < TREND_THRESHOLD) return { show: false }
  return {
    show: true,
    direction: trend > 0 ? 'up' : 'down',
    magnitude: Math.round(mag),
  }
}

/**
 * CSS class for a score, matching the red→white→blue gradient used elsewhere
 * (high score = elite contact = signal red, low score = cold = blue).
 *
 * Cutoffs intentionally match the percentile bands used in percentileColors.js
 * for visual consistency across the app.
 */
export function scoreColorClass(score) {
  if (score == null) return 'text-fg-500'
  if (score >= 90) return 'text-signal-400'      // elite
  if (score >= 75) return 'text-signal-200'      // strong
  if (score >= 50) return 'text-fg-700'          // average-plus
  if (score >= 30) return 'text-fg-600'          // below average
  if (score >= 10) return 'text-edge-cold-1'     // poor
  return 'text-edge-cold-2'                      // bottom of barrel
}
