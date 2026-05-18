/**
 * percentileColors.js — Color-code Statcast cells based on league percentile.
 *
 * Phase 7. Used by BatterTable to highlight elite/poor performance.
 *
 * Strategy: thresholds are hardcoded based on MLB-wide distributions for
 * 2024-2025 data (rough approximations from public Statcast leaderboards).
 * These don't need to be exact — they just need to be directionally correct
 * so that "elite" feels elite and "poor" feels poor.
 *
 * Two types of stats:
 *   higher_better: more is better (barrel_pct for batters, xslg, hard_hit_pct)
 *   lower_better: less is better (barrel_pct for PITCHERS — flipped!)
 *
 * Returns a Tailwind class for the text color.
 */

// Thresholds for BATTER stats (higher = better)
// Format: { stat: { elite: x, good: y, poor: z } }
// Values >= elite → bright signal (green / red brand color)
// Values >= good → light signal
// Values <= poor → cold blue
// Otherwise → neutral
const BATTER_THRESHOLDS = {
  barrel_pct:     { elite: 14, good: 9,  poor: 4   },
  hard_hit_pct:   { elite: 50, good: 42, poor: 32  },
  sweet_spot_pct: { elite: 38, good: 33, poor: 28  },
  xba:            { elite: 0.290, good: 0.260, poor: 0.220 },
  xslg:           { elite: 0.500, good: 0.430, poor: 0.360 },
  xwoba:          { elite: 0.380, good: 0.340, poor: 0.300 },
  ev_avg:         { elite: 92,    good: 89,    poor: 86    },
  ev_max:         { elite: 113,   good: 109,   poor: 104   },
}

// Thresholds for PITCHER allowed stats (lower = better for the pitcher)
// These get the SAME thresholds as batter but interpreted inverted
const PITCHER_THRESHOLDS = {
  barrel_pct:     { elite: 5,  good: 7,  poor: 11  },
  hard_hit_pct:   { elite: 34, good: 38, poor: 44  },
  sweet_spot_pct: { elite: 30, good: 33, poor: 36  },
  xba:            { elite: 0.220, good: 0.245, poor: 0.275 },
  xslg:           { elite: 0.350, good: 0.400, poor: 0.460 },
  xwoba:          { elite: 0.290, good: 0.320, poor: 0.355 },
  ev_avg:         { elite: 87,    good: 89,    poor: 91    },
  ev_max:         { elite: 105,   good: 109,   poor: 113   },
}

/**
 * Get Tailwind text color class for a stat value.
 *
 * @param {number|null} value - the stat value
 * @param {string} statKey - one of the keys above
 * @param {'batter'|'pitcher'} context - whether higher or lower is "better"
 * @returns {string} - Tailwind class like 'text-signal-400'
 */
export function statColor(value, statKey, context = 'batter') {
  if (value == null || isNaN(value)) return 'text-fg-500'

  const thresholds = context === 'pitcher' ? PITCHER_THRESHOLDS : BATTER_THRESHOLDS
  const t = thresholds[statKey]
  if (!t) return 'text-fg-600'

  if (context === 'batter') {
    // higher is better
    if (value >= t.elite) return 'text-signal-400'    // bright red brand
    if (value >= t.good)  return 'text-signal-200'    // light signal
    if (value <= t.poor)  return 'text-edge-cold-1'   // cold blue
    return 'text-fg-600'
  } else {
    // pitcher allowed — lower is better
    if (value <= t.elite) return 'text-signal-400'
    if (value <= t.good)  return 'text-signal-200'
    if (value >= t.poor)  return 'text-edge-cold-1'
    return 'text-fg-600'
  }
}

/**
 * Format a Statcast stat value with appropriate precision and dash for null.
 *   barrel_pct, hard_hit_pct, sweet_spot_pct → "14.2%"
 *   xba, xslg, xwoba → ".342"
 *   ev_avg, ev_max → "91.4"
 */
export function fmtStat(value, statKey) {
  if (value == null || isNaN(value)) return '—'

  const pctStats = ['barrel_pct', 'hard_hit_pct', 'sweet_spot_pct']
  const decimalStats = ['xba', 'xslg', 'xwoba']
  const evStats = ['ev_avg', 'ev_max']

  if (pctStats.includes(statKey)) {
    return `${value.toFixed(1)}%`
  }
  if (decimalStats.includes(statKey)) {
    // baseball-style: ".342" not "0.342"
    return value < 1
      ? `.${Math.round(value * 1000).toString().padStart(3, '0')}`
      : value.toFixed(3)
  }
  if (evStats.includes(statKey)) {
    return value.toFixed(1)
  }
  return String(value)
}
