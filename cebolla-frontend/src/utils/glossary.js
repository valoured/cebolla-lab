/**
 * glossary.js — Term definitions for InfoTooltip popups.
 *
 * Each entry has:
 *   label:       The display name (e.g. "Barrel %")
 *   unit:        Optional unit string (e.g. "%", "mph", "x")
 *   description: Plain-English explanation
 *   guide:       Optional array of thresholds with tone labels
 *                (used to show "elite / good / poor" ranges)
 *   note:        Optional context line (e.g. "Sample sizes apply")
 *
 * To add a new tooltip-able term:
 *   1. Add an entry here
 *   2. Use <InfoTooltip term="key_name" /> wherever the term appears
 */

export const glossary = {
  // ── Batter Statcast ──────────────────────────────────────
  barrel_pct: {
    label: 'Barrel %',
    unit: '%',
    description:
      "Percentage of batted balls that are 'barreled' — Statcast's flag for the optimal combo of exit velo and launch angle. Highest-quality contact a hitter can make. Strongest single predictor of homers.",
    guide: [
      { label: 'Elite',   value: '14%+',   tone: 'tone-elite' },
      { label: 'Good',    value: '9–13%',  tone: 'tone-good'  },
      { label: 'Average', value: '5–8%',   tone: 'tone-avg'   },
      { label: 'Poor',    value: 'Under 5%', tone: 'tone-poor' },
    ],
  },

  hard_hit_pct: {
    label: 'Hard-Hit %',
    unit: '%',
    description:
      'Percentage of batted balls with exit velocity 95+ mph. Quick gut-check on whether a hitter is squaring the ball up. Stabilizes faster than barrel %.',
    guide: [
      { label: 'Elite',   value: '50%+',    tone: 'tone-elite' },
      { label: 'Good',    value: '42–49%',  tone: 'tone-good'  },
      { label: 'Average', value: '33–41%',  tone: 'tone-avg'   },
      { label: 'Poor',    value: 'Under 33%', tone: 'tone-poor' },
    ],
  },

  xba: {
    label: 'xBA',
    unit: 'avg',
    description:
      'Expected Batting Average. What this hitter "should" be batting based on Statcast quality of contact (exit velo + launch angle), independent of defense and luck.',
    guide: [
      { label: 'Elite',   value: '.290+',    tone: 'tone-elite' },
      { label: 'Good',    value: '.260–.289', tone: 'tone-good' },
      { label: 'Average', value: '.230–.259', tone: 'tone-avg'  },
      { label: 'Poor',    value: 'Under .230', tone: 'tone-poor' },
    ],
  },

  xslg: {
    label: 'xSLG',
    unit: 'slug',
    description:
      'Expected Slugging Percentage. The power version of xBA. Strong predictor of HR markets when paired with Barrel %. Use this and Brl% together.',
    guide: [
      { label: 'Elite',   value: '.500+',    tone: 'tone-elite' },
      { label: 'Good',    value: '.430–.499', tone: 'tone-good' },
      { label: 'Average', value: '.370–.429', tone: 'tone-avg'  },
      { label: 'Poor',    value: 'Under .370', tone: 'tone-poor' },
    ],
  },

  xwoba: {
    label: 'xwOBA',
    unit: 'woba',
    description:
      'Expected Weighted On-Base Average. The most comprehensive Statcast-derived hitting metric — blends xBA, xSLG, and walks into one number.',
    guide: [
      { label: 'Elite',   value: '.380+',    tone: 'tone-elite' },
      { label: 'Good',    value: '.340–.379', tone: 'tone-good' },
      { label: 'Average', value: '.300–.339', tone: 'tone-avg'  },
      { label: 'Poor',    value: 'Under .300', tone: 'tone-poor' },
    ],
  },

  sweet_spot_pct: {
    label: 'Sweet Spot %',
    unit: '%',
    description:
      'Percentage of batted balls with launch angle between 8° and 32° — the optimal range for line drives and home runs.',
  },

  ev_avg: {
    label: 'Avg Exit Velo',
    unit: 'mph',
    description:
      'Average exit velocity across all batted balls. Higher = stronger contact. Less predictive than Hard-Hit % because outliers skew the average.',
  },

  ev_max: {
    label: 'Max Exit Velo',
    unit: 'mph',
    description:
      "Single hardest-hit ball of the window. Measures a hitter's CEILING — what they're capable of when they connect.",
  },

  // ── Pitcher Allowed ──────────────────────────────────────
  pitcher_barrel_pct: {
    label: 'Barrel % Allowed',
    unit: '%',
    description:
      'Percentage of batted balls against this pitcher that get barreled. Lower is better for the pitcher. When this is high (10%+), batters are squaring him up.',
    guide: [
      { label: 'Elite',   value: 'Under 5%', tone: 'tone-elite' },
      { label: 'Good',    value: '5–7%',     tone: 'tone-good'  },
      { label: 'Average', value: '8–10%',    tone: 'tone-avg'   },
      { label: 'Poor',    value: '10%+',     tone: 'tone-poor'  },
    ],
    note: 'Tip: target hitters facing pitchers in the "Poor" range.',
  },

  pitcher_hard_hit_pct: {
    label: 'Hard-Hit % Allowed',
    unit: '%',
    description:
      'Percentage of batted balls against this pitcher hit 95+ mph. Higher = batters are crushing him.',
    guide: [
      { label: 'Elite',   value: 'Under 34%', tone: 'tone-elite' },
      { label: 'Good',    value: '34–38%',    tone: 'tone-good'  },
      { label: 'Average', value: '39–43%',    tone: 'tone-avg'   },
      { label: 'Poor',    value: '44%+',      tone: 'tone-poor'  },
    ],
  },

  pitcher_xslg: {
    label: 'xSLG Allowed',
    unit: 'slug',
    description:
      'Expected slugging against this pitcher. Captures the power output batters are generating regardless of defense or luck.',
  },

  pitcher_xba: {
    label: 'xBA Allowed',
    unit: 'avg',
    description:
      'Expected batting average against this pitcher. Tells you if batters should be hitting him hard, regardless of actual results.',
  },

  // ── Betting Math ─────────────────────────────────────────
  edge: {
    label: 'Edge',
    description:
      "Our model's projected probability minus the sportsbook's no-vig probability (the 'true' probability after removing the book's margin). Positive edge = we think the bet is undervalued.",
    guide: [
      { label: 'Strong',  value: '5%+',     tone: 'tone-elite' },
      { label: 'Solid',   value: '2–5%',    tone: 'tone-good'  },
      { label: 'Neutral', value: '−2 to +2%', tone: 'tone-avg' },
      { label: 'Fade',    value: 'Under −2%', tone: 'tone-poor' },
    ],
    note: 'Edge is calculated against DraftKings opening lines, not the current market.',
  },

  proj_pct: {
    label: 'Projection %',
    description:
      "Our model's estimated probability of this outcome occurring. For HR markets, this is the probability the player hits at least one HR. Compare against the implied book probability for edge.",
  },

  no_vig: {
    label: 'No-Vig Probability',
    description:
      "The sportsbook's 'true' implied probability with their margin (vig) removed. The fair price they think this bet should be at.",
  },

  bvp: {
    label: 'BvP (Batter vs Pitcher)',
    description:
      "Historical results between this exact batter-pitcher matchup. Shown as HR/PA. Small samples — useful as flavor but never the basis of a bet on its own.",
    note: 'Most BvP samples are under 20 PA. Treat with caution.',
  },

  contact_score: {
    label: 'Contact Score',
    description:
      "A 0–100 composite score showing how a batter's recent (L14) contact quality ranks against all qualified MLB batters. Blends Barrel% (40%), Hard-Hit% (30%), and xSLG (30%) by percentile rank. Higher = better contact quality. The trend arrow (▲/▼N) shows how the L14 score compares to the batter's season baseline — flags hot and cold streaks.",
    guide: [
      { label: 'Elite',         value: '90+',   tone: 'tone-elite' },
      { label: 'Strong',        value: '75–89', tone: 'tone-good'  },
      { label: 'Average',       value: '50–74', tone: 'tone-avg'   },
      { label: 'Below Average', value: '30–49', tone: 'tone-poor'  },
      { label: 'Poor',          value: 'Under 30', tone: 'tone-poor' },
    ],
    note: 'Minimum 20 L14 PA to qualify. Trend arrow shows only when L14 differs from season by 5+ points.',
  },

  longshot: {
    label: 'Longshot',
    description:
      'Bets with odds beyond +2000. Our model is calibrated for primary markets; longshots become unreliable beyond +2000 because no-vig math gets distorted at the extremes.',
  },

  // ── Park & Weather ───────────────────────────────────────
  hr_factor: {
    label: 'HR Factor',
    unit: 'x',
    description:
      'Park HR factor adjusted for today\'s weather (wind, temp, humidity, precipitation). 1.00 = league-average park. Values above 1 boost HR probability; below 1 suppress it.',
    guide: [
      { label: 'Hot Park', value: '1.05+',    tone: 'tone-elite' },
      { label: 'Neutral',  value: '0.95–1.05', tone: 'tone-avg'  },
      { label: 'Cold Park', value: 'Under 0.95', tone: 'tone-poor' },
    ],
  },

  hr_factor_lhb: {
    label: 'LHB HR Factor',
    unit: 'x',
    description:
      'Today\'s HR factor specifically for left-handed batters. Some parks (e.g. Yankee Stadium, Citizens Bank) play very differently for LHB vs RHB due to fence dimensions.',
  },

  hr_factor_rhb: {
    label: 'RHB HR Factor',
    unit: 'x',
    description:
      'Today\'s HR factor specifically for right-handed batters. Pull-friendly parks favor opposite-handed hitters.',
  },

  // ── Statcast Windows ─────────────────────────────────────
  window_season: {
    label: 'Season Window',
    description:
      'All of this season\'s data to date. Stable baseline but slow to reflect recent form. Use as the projection anchor.',
  },

  window_l30: {
    label: 'L30 Window',
    description:
      'Last 30 days. Mid-range window — catches the past month of trends without going too small-sample.',
  },

  window_l14: {
    label: 'L14 Window',
    description:
      'Last 14 days (default). ~50–60 PAs for regulars. Sweet spot of "big enough to mean something, recent enough to capture form." Use this to find heaters and slumps.',
  },

  window_l7: {
    label: 'L7 Window',
    description:
      'Last 7 days. ~25–30 PAs. Useful for spotting hot streaks but noisy — confirm with L14 before acting.',
  },
}
