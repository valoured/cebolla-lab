<script setup>
/**
 * StatsView.vue — Stats & Studies
 *
 * Real credibility infrastructure. The page someone visits when they ask
 * "okay, but does the model actually work?" Shows:
 *
 *   1. All-time scoreboard (total settled, W/L/P/V, net P&L, ROI)
 *   2. Per-market breakdown (HR vs HRR)
 *   3. Calibration plot — does projected prob match actual hit rate?
 *      (the most important credibility test)
 *   4. Per-odds-tier breakdown — how do longshots vs favorites perform?
 *   5. Monthly P&L timeline
 *
 * Sources: BOTH `pods` (straights/single-leg picks) AND `cards` (parlays).
 * Normalized into a unified `picks` array so all downstream math treats
 * them uniformly.
 *
 * Cards count as ONE pick each (the parlay as a unit), not N picks for N
 * legs. Calibration uses combined_prob; tier breakdown uses combined_odds.
 * This matches how a real bettor would evaluate their record — by the
 * tickets they wrote, not the legs on them.
 */

import { ref, computed, onMounted } from 'vue'
import { supabase } from '../supabase.js'
import LoadingBrand from '../components/LoadingBrand.vue'

const pods = ref([])
const cards = ref([])
const loading = ref(true)
const error = ref(null)

// ── Load ──────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    const [podsRes, cardsRes] = await Promise.all([
      supabase
        .from('pods')
        .select('id, pod_date, market_class, market, projected_prob, no_vig_prob, ' +
                'edge, american_odds, status, payout, stake, contact_score, combined_score, ' +
                'player_name, team_abbrev, opponent_abbrev, settled_at, ' +
                'closing_odds, closing_implied, closing_no_vig, clv_raw, clv_no_vig')
        .order('pod_date', { ascending: true })
        .limit(2000),
      supabase
        .from('cards')
        .select('id, card_date, tier, label, leg_count, combined_prob, ' +
                'combined_odds, decimal_odds, edge, ev_per_dollar, stake_rec, ' +
                'payout_if_hit, status, payout, settled_at')
        .order('card_date', { ascending: true })
        .limit(2000),
    ])
    if (podsRes.error) throw podsRes.error
    if (cardsRes.error) throw cardsRes.error
    pods.value = podsRes.data || []
    cards.value = cardsRes.data || []
  } catch (e) {
    console.error('[StatsView] load failed:', e)
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

// ── Normalize pods + cards into unified `picks` ───────────────
// Each pick has: date, market_class, projected_prob, american_odds,
// status, payout, stake.
const picks = computed(() => {
  const out = []
  for (const p of pods.value) {
    out.push({
      kind: 'pod',
      tier: 'straight',
      date: p.pod_date,
      market_class: p.market_class || 'hr',
      projected_prob: Number(p.projected_prob),
      american_odds: Number(p.american_odds),
      status: p.status,
      payout: Number(p.payout) || 0,
      stake: Number(p.stake) || 10,
      settled_at: p.settled_at,
    })
  }
  for (const c of cards.value) {
    // Cards count as one pick each (the parlay as a unit). market_class
    // = the tier ('two_leg' etc.) so per-market breakdown can split
    // straights vs parlays cleanly.
    out.push({
      kind: 'card',
      tier: c.tier,
      date: c.card_date,
      market_class: c.tier,  // 'straight' | 'two_leg' | 'three_leg' | 'four_leg'
      projected_prob: Number(c.combined_prob),
      american_odds: Number(c.combined_odds),
      status: c.status,
      payout: Number(c.payout) || 0,
      stake: Number(c.stake_rec) || 10,
      settled_at: c.settled_at,
    })
  }
  return out
})

// ── Derived: settled picks ────────────────────────────────────
const settled = computed(() =>
  picks.value.filter(p => ['win', 'loss', 'push', 'void'].includes(p.status))
)
const settledNonVoid = computed(() =>
  settled.value.filter(p => p.status !== 'void')
)

// ── All-time scoreboard ───────────────────────────────────────
function recordFor(list) {
  let w = 0, l = 0, p = 0, v = 0
  for (const pod of list) {
    if (pod.status === 'win') w++
    else if (pod.status === 'loss') l++
    else if (pod.status === 'push') p++
    else if (pod.status === 'void') v++
  }
  return { w, l, p, v, total: list.length, settled: w + l + p + v }
}

function pnlFor(list) {
  return list.reduce((sum, pod) => sum + (Number(pod.payout) || 0), 0)
}

function totalStake(list) {
  return list.reduce((sum, pod) => sum + (Number(pod.stake) || 0), 0)
}

function roiFor(list) {
  const stake = totalStake(list)
  if (!stake) return null
  return pnlFor(list) / stake * 100
}

function hitRateFor(list) {
  const nonVoid = list.filter(p => p.status !== 'void')
  if (!nonVoid.length) return null
  const wins = nonVoid.filter(p => p.status === 'win').length
  return (wins / nonVoid.length) * 100
}

const overallRecord = computed(() => recordFor(settled.value))
const overallPnl = computed(() => pnlFor(settled.value))
const overallStake = computed(() => totalStake(settled.value))
const overallRoi = computed(() => roiFor(settled.value))
const overallHitRate = computed(() => hitRateFor(settled.value))

// ── Per-market breakdown ──────────────────────────────────────
// Slices the unified settled picks by their market_class:
//   - PODs use 'hr' / 'hrr'
//   - Cards use 'straight' / 'two_leg' / 'three_leg' / 'four_leg'
// (After unification, every pick has a market_class. Card 'straight' tier
// would only show up if we start writing pods to the cards table too —
// for now PODs live in pods, cards live in cards, so cards.tier never =
// 'straight' in practice.)
function marketSlice(mc) {
  return settled.value.filter(p => (p.market_class || 'hr') === mc)
}
function buildStats(list) {
  return {
    record: recordFor(list),
    pnl: pnlFor(list),
    roi: roiFor(list),
    hitRate: hitRateFor(list),
  }
}
const hrStats        = computed(() => buildStats(marketSlice('hr')))
const hrrStats       = computed(() => buildStats(marketSlice('hrr')))
const twoLegStats    = computed(() => buildStats(marketSlice('two_leg')))
const threeLegStats  = computed(() => buildStats(marketSlice('three_leg')))
const fourLegStats   = computed(() => buildStats(marketSlice('four_leg')))

// Convenience flag — only show parlay breakdown section if there's any
// parlay activity in the data set.
const hasParlayData = computed(() =>
  twoLegStats.value.record.settled +
  threeLegStats.value.record.settled +
  fourLegStats.value.record.settled > 0
)

// ── Calibration plot ──────────────────────────────────────────
// Bucket settled picks by projected_prob in 10pp ranges. For each bucket,
// compare avg projected_prob to actual hit rate. A well-calibrated model
// has dots near the diagonal y=x line. Buckets with fewer than 3 picks
// are shown lighter / smaller because the rate is unreliable.
const CALIBRATION_BUCKETS = [
  { lo: 0,    hi: 0.10, label: '0-10%'   },
  { lo: 0.10, hi: 0.20, label: '10-20%'  },
  { lo: 0.20, hi: 0.30, label: '20-30%'  },
  { lo: 0.30, hi: 0.40, label: '30-40%'  },
  { lo: 0.40, hi: 0.50, label: '40-50%'  },
  { lo: 0.50, hi: 0.60, label: '50-60%'  },
  { lo: 0.60, hi: 0.70, label: '60-70%'  },
  { lo: 0.70, hi: 0.80, label: '70-80%'  },
  { lo: 0.80, hi: 1.01, label: '80-100%' },
]
const calibration = computed(() => {
  const out = []
  for (const b of CALIBRATION_BUCKETS) {
    const inBucket = settledNonVoid.value.filter(p => {
      const pp = Number(p.projected_prob)
      return Number.isFinite(pp) && pp >= b.lo && pp < b.hi
    })
    if (inBucket.length === 0) {
      out.push({ ...b, count: 0, avgProjected: null, actualHitRate: null })
      continue
    }
    const avgProj = inBucket.reduce((s, p) => s + Number(p.projected_prob), 0) / inBucket.length
    const wins = inBucket.filter(p => p.status === 'win').length
    out.push({
      ...b,
      count: inBucket.length,
      avgProjected: avgProj * 100,
      actualHitRate: (wins / inBucket.length) * 100,
    })
  }
  return out
})

// ── Closing Line Value (CLV) ──────────────────────────────────
// CLV measures whether we locked at a better price than where the market
// ultimately settled. Positive CLV = we beat the close = strong signal of
// model edge. Stabilizes much faster than W/L (50-100 picks vs months).
//
// We use clv_no_vig (de-vigged) as the canonical metric. clv_raw is also
// available but it doesn't normalize for changes in market vig — if the
// book just tightened/loosened juice between lock and close, clv_raw will
// move even though no real edge changed. clv_no_vig isolates the real
// probability shift.
//
// Only includes pods that have captured closing odds (filtered on clv_no_vig
// being non-null). Card-leg CLV is computed but not yet displayed — a future
// iteration can add per-tier card CLV.
const podsWithCLV = computed(() =>
  pods.value.filter(p => p.clv_no_vig != null)
)

function mean(arr) {
  if (!arr.length) return null
  return arr.reduce((s, v) => s + v, 0) / arr.length
}

const clvSummary = computed(() => {
  const all = podsWithCLV.value.map(p => Number(p.clv_no_vig))
  const total = all.length
  if (!total) return { total: 0, mean: null, positive: 0, pctPositive: null, hr: null, hrr: null }

  const positive = all.filter(v => v > 0).length
  const hr = podsWithCLV.value
    .filter(p => (p.market_class || 'hr') === 'hr')
    .map(p => Number(p.clv_no_vig))
  const hrr = podsWithCLV.value
    .filter(p => (p.market_class || 'hr') === 'hrr')
    .map(p => Number(p.clv_no_vig))

  return {
    total,
    mean: mean(all),
    positive,
    pctPositive: (positive / total) * 100,
    hr: hr.length ? { count: hr.length, mean: mean(hr) } : null,
    hrr: hrr.length ? { count: hrr.length, mean: mean(hrr) } : null,
  }
})

// Histogram bins in percentage-point terms.
// CLV is theoretically in [-1, +1] (difference of two probabilities).
// Practically observed range is roughly [-0.10, +0.10]. We use sentinel
// outer bounds well past observed values so no real CLV value can fall
// outside a bin. Upper bound is 1.01 (not 1.00) so exact-1.0 still matches.
const CLV_BINS = [
  { lo: -1.01, hi: -0.05, label: '<-5pp'  },  // catch-all for big negatives
  { lo: -0.05, hi: -0.03, label: '-5/-3'  },
  { lo: -0.03, hi: -0.01, label: '-3/-1'  },
  { lo: -0.01, hi:  0.01, label: '-1/+1'  },
  { lo:  0.01, hi:  0.03, label: '+1/+3'  },
  { lo:  0.03, hi:  0.05, label: '+3/+5'  },
  { lo:  0.05, hi:  0.10, label: '+5/+10' },
  { lo:  0.10, hi:  1.01, label: '>+10pp' },  // catch-all for big positives
]
const clvHistogram = computed(() => {
  const data = podsWithCLV.value
  return CLV_BINS.map(b => {
    const inBin = data.filter(p => {
      const v = Number(p.clv_no_vig)
      return v >= b.lo && v < b.hi
    })
    return { ...b, count: inBin.length }
  })
})

// Convenience: max bar height for histogram rendering
const clvHistogramMax = computed(() =>
  Math.max(1, ...clvHistogram.value.map(b => b.count))
)


const ODDS_TIERS = [
  { key: 'fav',  label: 'Favorite (sub +150)',    test: o => o < 150 },
  { key: 'mid',  label: 'Mid (+150 to +299)',     test: o => o >= 150 && o < 300 },
  { key: 'long', label: 'Longshot (+300 to +499)',test: o => o >= 300 && o < 500 },
  { key: 'lott', label: 'Lottery (+500+)',        test: o => o >= 500 },
]
const tierBreakdown = computed(() => {
  return ODDS_TIERS.map(tier => {
    const list = settled.value.filter(p => {
      const o = Number(p.american_odds)
      return Number.isFinite(o) && tier.test(o)
    })
    return {
      ...tier,
      record: recordFor(list),
      pnl: pnlFor(list),
      roi: roiFor(list),
      hitRate: hitRateFor(list),
    }
  })
})

// ── Monthly P&L timeline ──────────────────────────────────────
const monthlyPnl = computed(() => {
  const byMonth = new Map()
  for (const pick of settled.value) {
    const dt = pick.settled_at || pick.date
    if (!dt) continue
    const key = String(dt).slice(0, 7) // YYYY-MM
    if (!byMonth.has(key)) {
      byMonth.set(key, { month: key, picks: 0, wins: 0, pnl: 0 })
    }
    const bucket = byMonth.get(key)
    bucket.picks++
    if (pick.status === 'win') bucket.wins++
    bucket.pnl += Number(pick.payout) || 0
  }
  return Array.from(byMonth.values()).sort((a, b) => a.month.localeCompare(b.month))
})

// ── Formatters ────────────────────────────────────────────────
function fmtMoney(n, signed = true) {
  if (n == null || !Number.isFinite(n)) return '—'
  const sign = signed && n > 0 ? '+' : (n < 0 ? '-' : '')
  return `${sign}$${Math.abs(n).toFixed(2)}`
}
function fmtPct(n) {
  if (n == null || !Number.isFinite(n)) return '—'
  return `${n.toFixed(1)}%`
}
function fmtMonth(s) {
  if (!s) return '—'
  const [y, m] = s.split('-')
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${months[parseInt(m, 10) - 1]} ${y.slice(2)}`
}

// ── Calibration plot SVG layout ───────────────────────────────
const CAL_SIZE = 320
const CAL_PAD = 32
const CAL_INNER = CAL_SIZE - 2 * CAL_PAD

function calX(projPct) {
  // 0-100% → 0-CAL_INNER
  return CAL_PAD + (projPct / 100) * CAL_INNER
}
function calY(hitPct) {
  // 0-100% → flipped (SVG y goes down)
  return CAL_PAD + ((100 - hitPct) / 100) * CAL_INNER
}

// ── Monthly bar chart layout ──────────────────────────────────
const BAR_HEIGHT = 80
const BAR_PAD = 8

const monthlyChartMax = computed(() => {
  const all = monthlyPnl.value.flatMap(m => [Math.abs(m.pnl)])
  if (!all.length) return 1
  return Math.max(...all, 10) // floor at $10 for visual scale
})

function barY(pnl) {
  // Positive bars grow upward from middle, negative downward
  const max = monthlyChartMax.value
  const halfHeight = BAR_HEIGHT / 2
  const pct = Math.min(1, Math.abs(pnl) / max)
  return pct * halfHeight
}
</script>

<template>
  <div class="min-h-screen">
    <!-- HEADER -->
    <section class="px-4 sm:px-6 pt-6 pb-4">
      <div class="flex items-baseline gap-3 mb-2">
        <h1 class="display-text text-2xl sm:text-3xl text-fg-800">Stats &amp; Studies</h1>
        <span class="label-bracket text-fg-500">M.04</span>
      </div>
      <p class="text-fg-500 text-sm max-w-2xl">
        Receipts. Every settled Cebolla pick, broken down by market, calibration, odds tier, and month.
        The model's job is to be right on average — these are the numbers that prove it.
      </p>
    </section>

    <LoadingBrand v-if="loading" />

    <div v-else-if="error" class="px-6 py-12 text-edge-cold-1">
      Error: {{ error }}
    </div>

    <div v-else-if="overallRecord.settled === 0" class="px-6 py-12">
      <div class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
        <div class="display-text text-lg text-fg-500 italic mb-2">No settled picks yet</div>
        <p class="text-fg-500 text-xs">
          Once Cebolla starts settling picks (after games go Final), the full stats
          and calibration analysis will populate here.
        </p>
      </div>
    </div>

    <template v-else>
      <!-- ── ALL-TIME SCOREBOARD ─────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-8">
        <div class="flex items-baseline gap-3 mb-3">
          <h2 class="display-text text-xl text-fg-800">All-Time</h2>
          <span class="label-bracket !text-[8px] text-fg-500">M.04.a</span>
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
          <div class="bg-bg-50 border border-bg-200 px-3 py-2">
            <div class="label-caps !text-[9px]">Record (W-L-P)</div>
            <div class="display-num text-2xl text-fg-800 mt-1">
              {{ overallRecord.w }}-{{ overallRecord.l }}<span v-if="overallRecord.p" class="text-fg-500 text-xs">-{{ overallRecord.p }}</span>
            </div>
          </div>
          <div class="bg-bg-50 border border-bg-200 px-3 py-2">
            <div class="label-caps !text-[9px]">Net P&amp;L</div>
            <div class="display-num text-2xl mt-1"
                 :class="overallPnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
              {{ fmtMoney(overallPnl) }}
            </div>
          </div>
          <div class="bg-bg-50 border border-bg-200 px-3 py-2">
            <div class="label-caps !text-[9px]">ROI</div>
            <div class="display-num text-2xl mt-1"
                 :class="overallRoi == null ? 'text-fg-500' : (overallRoi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
              {{ overallRoi != null ? (overallRoi >= 0 ? '+' : '') + fmtPct(overallRoi) : '—' }}
            </div>
          </div>
          <div class="bg-bg-50 border border-bg-200 px-3 py-2">
            <div class="label-caps !text-[9px]">Hit Rate</div>
            <div class="display-num text-2xl text-fg-800 mt-1">
              {{ fmtPct(overallHitRate) }}
            </div>
          </div>
        </div>

        <!-- Per-market breakdown -->
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div class="bg-bg-50 border border-bg-200 px-4 py-3">
            <div class="flex items-baseline justify-between mb-2">
              <div class="display-text text-sm text-fg-800">HR Anytime</div>
              <span class="label-caps !text-[9px] text-fg-500">{{ hrStats.record.settled }} settled</span>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div class="label-caps !text-[8px]">W-L</div>
                <div class="display-num text-fg-800 mt-0.5">{{ hrStats.record.w }}-{{ hrStats.record.l }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">ROI</div>
                <div class="display-num mt-0.5"
                     :class="hrStats.roi == null ? 'text-fg-500' : (hrStats.roi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
                  {{ hrStats.roi != null ? (hrStats.roi >= 0 ? '+' : '') + fmtPct(hrStats.roi) : '—' }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">P&amp;L</div>
                <div class="display-num mt-0.5"
                     :class="hrStats.pnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
                  {{ fmtMoney(hrStats.pnl) }}
                </div>
              </div>
            </div>
          </div>
          <div class="bg-bg-50 border border-bg-200 px-4 py-3">
            <div class="flex items-baseline justify-between mb-2">
              <div class="display-text text-sm text-fg-800">H + R + RBI</div>
              <span class="label-caps !text-[9px] text-fg-500">{{ hrrStats.record.settled }} settled</span>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div class="label-caps !text-[8px]">W-L</div>
                <div class="display-num text-fg-800 mt-0.5">{{ hrrStats.record.w }}-{{ hrrStats.record.l }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">ROI</div>
                <div class="display-num mt-0.5"
                     :class="hrrStats.roi == null ? 'text-fg-500' : (hrrStats.roi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
                  {{ hrrStats.roi != null ? (hrrStats.roi >= 0 ? '+' : '') + fmtPct(hrrStats.roi) : '—' }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">P&amp;L</div>
                <div class="display-num mt-0.5"
                     :class="hrrStats.pnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
                  {{ fmtMoney(hrrStats.pnl) }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Parlay breakdown — only render if we have any parlay data -->
        <div v-if="hasParlayData" class="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3">
          <div class="bg-bg-50 border border-bg-200 px-4 py-3">
            <div class="flex items-baseline justify-between mb-2">
              <div class="display-text text-sm text-fg-800">2-Leggers</div>
              <span class="label-caps !text-[9px] text-fg-500">{{ twoLegStats.record.settled }} settled</span>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div class="label-caps !text-[8px]">W-L</div>
                <div class="display-num text-fg-800 mt-0.5">{{ twoLegStats.record.w }}-{{ twoLegStats.record.l }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">ROI</div>
                <div class="display-num mt-0.5"
                     :class="twoLegStats.roi == null ? 'text-fg-500' : (twoLegStats.roi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
                  {{ twoLegStats.roi != null ? (twoLegStats.roi >= 0 ? '+' : '') + fmtPct(twoLegStats.roi) : '—' }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">P&amp;L</div>
                <div class="display-num mt-0.5"
                     :class="twoLegStats.pnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
                  {{ fmtMoney(twoLegStats.pnl) }}
                </div>
              </div>
            </div>
          </div>
          <div class="bg-bg-50 border border-bg-200 px-4 py-3">
            <div class="flex items-baseline justify-between mb-2">
              <div class="display-text text-sm text-fg-800">3-Leggers</div>
              <span class="label-caps !text-[9px] text-fg-500">{{ threeLegStats.record.settled }} settled</span>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div class="label-caps !text-[8px]">W-L</div>
                <div class="display-num text-fg-800 mt-0.5">{{ threeLegStats.record.w }}-{{ threeLegStats.record.l }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">ROI</div>
                <div class="display-num mt-0.5"
                     :class="threeLegStats.roi == null ? 'text-fg-500' : (threeLegStats.roi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
                  {{ threeLegStats.roi != null ? (threeLegStats.roi >= 0 ? '+' : '') + fmtPct(threeLegStats.roi) : '—' }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">P&amp;L</div>
                <div class="display-num mt-0.5"
                     :class="threeLegStats.pnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
                  {{ fmtMoney(threeLegStats.pnl) }}
                </div>
              </div>
            </div>
          </div>
          <div class="bg-bg-50 border border-bg-200 px-4 py-3">
            <div class="flex items-baseline justify-between mb-2">
              <div class="display-text text-sm text-fg-800">Lottery</div>
              <span class="label-caps !text-[9px] text-fg-500">{{ fourLegStats.record.settled }} settled</span>
            </div>
            <div class="grid grid-cols-3 gap-2 text-xs">
              <div>
                <div class="label-caps !text-[8px]">W-L</div>
                <div class="display-num text-fg-800 mt-0.5">{{ fourLegStats.record.w }}-{{ fourLegStats.record.l }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">ROI</div>
                <div class="display-num mt-0.5"
                     :class="fourLegStats.roi == null ? 'text-fg-500' : (fourLegStats.roi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
                  {{ fourLegStats.roi != null ? (fourLegStats.roi >= 0 ? '+' : '') + fmtPct(fourLegStats.roi) : '—' }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">P&amp;L</div>
                <div class="display-num mt-0.5"
                     :class="fourLegStats.pnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
                  {{ fmtMoney(fourLegStats.pnl) }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ── CALIBRATION PLOT ────────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-8">
        <div class="flex items-baseline gap-3 mb-1">
          <h2 class="display-text text-xl text-fg-800">Calibration</h2>
          <span class="label-bracket !text-[8px] text-fg-500">M.04.b</span>
        </div>
        <p class="text-fg-500 text-xs max-w-2xl mb-3">
          The most important credibility test. If the model says "25% chance to hit," does it actually
          hit 25% of the time over many picks? A perfectly calibrated model sits on the diagonal line.
          Above the line = under-projecting. Below the line = over-projecting.
        </p>

        <div class="bg-bg-50 border border-bg-200 p-4 inline-block">
          <svg :viewBox="`0 0 ${CAL_SIZE} ${CAL_SIZE}`" :width="CAL_SIZE" :height="CAL_SIZE" class="block">
            <!-- Grid -->
            <g opacity="0.15">
              <line v-for="i in 10" :key="`gx${i}`"
                    :x1="CAL_PAD + i * (CAL_INNER / 10)"
                    :x2="CAL_PAD + i * (CAL_INNER / 10)"
                    :y1="CAL_PAD"
                    :y2="CAL_PAD + CAL_INNER"
                    stroke="rgba(255,255,255,0.5)" stroke-width="0.5" />
              <line v-for="i in 10" :key="`gy${i}`"
                    :x1="CAL_PAD"
                    :x2="CAL_PAD + CAL_INNER"
                    :y1="CAL_PAD + i * (CAL_INNER / 10)"
                    :y2="CAL_PAD + i * (CAL_INNER / 10)"
                    stroke="rgba(255,255,255,0.5)" stroke-width="0.5" />
            </g>

            <!-- Diagonal (perfect calibration) -->
            <line :x1="CAL_PAD" :y1="CAL_PAD + CAL_INNER"
                  :x2="CAL_PAD + CAL_INNER" :y2="CAL_PAD"
                  stroke="rgba(255, 42, 42, 0.40)" stroke-width="1" stroke-dasharray="3 3" />

            <!-- Axes -->
            <line :x1="CAL_PAD" :y1="CAL_PAD" :x2="CAL_PAD" :y2="CAL_PAD + CAL_INNER"
                  stroke="rgba(255,255,255,0.30)" stroke-width="1" />
            <line :x1="CAL_PAD" :y1="CAL_PAD + CAL_INNER" :x2="CAL_PAD + CAL_INNER" :y2="CAL_PAD + CAL_INNER"
                  stroke="rgba(255,255,255,0.30)" stroke-width="1" />

            <!-- Axis labels -->
            <text :x="CAL_PAD + CAL_INNER / 2" :y="CAL_SIZE - 6"
                  text-anchor="middle"
                  font-family="JetBrains Mono, monospace" font-size="9" fill="rgba(255,255,255,0.55)">
              projected probability
            </text>
            <text :x="-CAL_PAD - CAL_INNER / 2" :y="10"
                  text-anchor="middle" transform="rotate(-90)"
                  font-family="JetBrains Mono, monospace" font-size="9" fill="rgba(255,255,255,0.55)">
              actual hit rate
            </text>

            <!-- Data points -->
            <g v-for="b in calibration" :key="b.label">
              <circle v-if="b.count > 0"
                      :cx="calX(b.avgProjected)" :cy="calY(b.actualHitRate)"
                      :r="Math.max(3, Math.min(12, 2 + b.count * 0.8))"
                      :fill="b.count >= 3 ? '#FF2A2A' : 'rgba(255, 42, 42, 0.30)'"
                      :stroke="b.count >= 3 ? 'rgba(255, 42, 42, 0.8)' : 'rgba(255, 42, 42, 0.4)'"
                      stroke-width="1" />
            </g>
          </svg>

          <!-- Bucket table -->
          <div class="mt-3 grid grid-cols-3 gap-x-3 gap-y-1 text-[10px] font-mono max-w-md">
            <div class="label-caps !text-[8px] opacity-60">range</div>
            <div class="label-caps !text-[8px] opacity-60 text-right">n / proj</div>
            <div class="label-caps !text-[8px] opacity-60 text-right">actual</div>
            <template v-for="b in calibration" :key="b.label">
              <div class="text-fg-500">{{ b.label }}</div>
              <div class="text-right" :class="b.count > 0 ? 'text-fg-700' : 'text-fg-400'">
                {{ b.count }}
                <span class="text-fg-500" v-if="b.count > 0"> · {{ b.avgProjected.toFixed(0) }}%</span>
              </div>
              <div class="text-right"
                   :class="b.count > 0 ? 'text-fg-700' : 'text-fg-400'">
                <span v-if="b.count > 0">{{ b.actualHitRate.toFixed(0) }}%</span>
                <span v-else>—</span>
              </div>
            </template>
          </div>
        </div>
      </section>

      <!-- ── CLOSING LINE VALUE ──────────────────────────────── -->
      <section v-if="clvSummary.total > 0" class="px-4 sm:px-6 mb-8">
        <div class="flex items-baseline gap-3 mb-1">
          <h2 class="display-text text-xl text-fg-800">Closing Line Value</h2>
          <span class="label-bracket !text-[8px] text-fg-500">M.04.b2</span>
        </div>
        <p class="text-fg-500 text-xs max-w-2xl mb-3">
          The sharpest short-term signal of model quality &mdash; long before settlement variance
          smooths out. Positive CLV means we locked at a better price than where the market ultimately
          settled. Consistently positive across many picks = the model is finding real mispricing
          that the rest of the market subsequently agrees with.
        </p>

        <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <!-- Summary stats -->
          <div class="bg-bg-50 border border-bg-200 p-4">
            <div class="label-caps mb-3">Overall</div>

            <div class="mb-3">
              <div class="label-caps !text-[9px] opacity-70 mb-0.5">Mean CLV (no-vig)</div>
              <div class="display-num text-2xl"
                   :class="clvSummary.mean > 0 ? 'text-signal-300' : clvSummary.mean < 0 ? 'text-edge-cold-1' : 'text-fg-600'">
                {{ clvSummary.mean !== null
                   ? (clvSummary.mean > 0 ? '+' : '') + (clvSummary.mean * 100).toFixed(2) + 'pp'
                   : '—' }}
              </div>
              <div class="text-[10px] text-fg-400 mt-0.5">
                across {{ clvSummary.total }} pod{{ clvSummary.total === 1 ? '' : 's' }} with captured closing odds
              </div>
            </div>

            <div class="mb-3 pb-3 border-b border-bg-200">
              <div class="label-caps !text-[9px] opacity-70 mb-0.5">% Positive</div>
              <div class="display-num text-xl"
                   :class="clvSummary.pctPositive >= 55 ? 'text-signal-300' : clvSummary.pctPositive >= 45 ? 'text-fg-600' : 'text-edge-cold-1'">
                {{ clvSummary.pctPositive !== null ? clvSummary.pctPositive.toFixed(0) + '%' : '—' }}
              </div>
              <div class="text-[10px] text-fg-400 mt-0.5">
                {{ clvSummary.positive }} of {{ clvSummary.total }} beat their close
              </div>
            </div>

            <div v-if="clvSummary.hr || clvSummary.hrr" class="space-y-2">
              <div v-if="clvSummary.hr" class="flex items-baseline justify-between">
                <span class="label-caps !text-[9px]">HR ({{ clvSummary.hr.count }})</span>
                <span class="display-num text-sm"
                      :class="clvSummary.hr.mean > 0 ? 'text-signal-300' : clvSummary.hr.mean < 0 ? 'text-edge-cold-1' : 'text-fg-600'">
                  {{ (clvSummary.hr.mean > 0 ? '+' : '') + (clvSummary.hr.mean * 100).toFixed(2) }}pp
                </span>
              </div>
              <div v-if="clvSummary.hrr" class="flex items-baseline justify-between">
                <span class="label-caps !text-[9px]">HRR ({{ clvSummary.hrr.count }})</span>
                <span class="display-num text-sm"
                      :class="clvSummary.hrr.mean > 0 ? 'text-signal-300' : clvSummary.hrr.mean < 0 ? 'text-edge-cold-1' : 'text-fg-600'">
                  {{ (clvSummary.hrr.mean > 0 ? '+' : '') + (clvSummary.hrr.mean * 100).toFixed(2) }}pp
                </span>
              </div>
            </div>
          </div>

          <!-- Histogram -->
          <div class="bg-bg-50 border border-bg-200 p-4 lg:col-span-2">
            <div class="label-caps mb-3">Distribution</div>
            <div class="space-y-1.5">
              <div v-for="bin in clvHistogram" :key="bin.label" class="grid grid-cols-[60px_1fr_30px] items-center gap-2">
                <span class="display-num text-[10px] text-right pr-1"
                      :class="bin.lo >= 0.01 ? 'text-signal-300' : bin.hi <= -0.01 ? 'text-edge-cold-1' : 'text-fg-500'">
                  {{ bin.label }}
                </span>
                <div class="h-4 bg-bg-100 rounded-sm overflow-hidden">
                  <div class="h-full transition-all"
                       :class="bin.lo >= 0.01 ? 'bg-signal-400/70' : bin.hi <= -0.01 ? 'bg-edge-cold-2/70' : 'bg-fg-400/40'"
                       :style="{ width: ((bin.count / clvHistogramMax) * 100) + '%' }">
                  </div>
                </div>
                <span class="display-num text-[10px] text-fg-500 text-right">{{ bin.count }}</span>
              </div>
            </div>
            <div class="mt-3 pt-3 border-t border-bg-200 text-[10px] text-fg-400 leading-relaxed">
              CLV (no-vig) = closing no-vig probability &minus; lock-time no-vig probability. Both sides de-vigged
              with the same curve so the comparison is apples-to-apples. Bins in percentage points.
              Numbers right of zero mean we found edges the market later agreed with.
            </div>
          </div>
        </div>
      </section>

      <!-- ── ODDS TIER BREAKDOWN ─────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-8">
        <div class="flex items-baseline gap-3 mb-1">
          <h2 class="display-text text-xl text-fg-800">By Odds Tier</h2>
          <span class="label-bracket !text-[8px] text-fg-500">M.04.c</span>
        </div>
        <p class="text-fg-500 text-xs max-w-2xl mb-3">
          Do longshots actually pay off, or do they drag the model down? Sliced by American odds.
        </p>

        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left border-b border-bg-200">
                <th class="label-caps !text-[8px] py-2 px-3">Tier</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">Picks</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">W-L</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">Hit %</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">P&amp;L</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">ROI</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in tierBreakdown" :key="t.key"
                  class="border-b border-bg-200/40">
                <td class="py-2 px-3 text-fg-700">{{ t.label }}</td>
                <td class="py-2 px-2 text-right display-num text-fg-700">{{ t.record.settled }}</td>
                <td class="py-2 px-2 text-right display-num text-fg-700">
                  <span v-if="t.record.settled">{{ t.record.w }}-{{ t.record.l }}</span>
                  <span v-else class="text-fg-400">—</span>
                </td>
                <td class="py-2 px-2 text-right display-num text-fg-700">{{ fmtPct(t.hitRate) }}</td>
                <td class="py-2 px-2 text-right display-num"
                    :class="t.pnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
                  {{ t.record.settled ? fmtMoney(t.pnl) : '—' }}
                </td>
                <td class="py-2 px-2 text-right display-num"
                    :class="t.roi == null ? 'text-fg-500' : (t.roi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
                  {{ t.roi != null ? (t.roi >= 0 ? '+' : '') + fmtPct(t.roi) : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- ── MONTHLY P&L TIMELINE ────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-10">
        <div class="flex items-baseline gap-3 mb-1">
          <h2 class="display-text text-xl text-fg-800">Monthly</h2>
          <span class="label-bracket !text-[8px] text-fg-500">M.04.d</span>
        </div>
        <p class="text-fg-500 text-xs max-w-2xl mb-3">
          Net P&amp;L per month. Bars above zero = profitable month, below = losing month.
        </p>

        <div class="bg-bg-50 border border-bg-200 px-4 py-3">
          <div class="flex items-end gap-2 overflow-x-auto" :style="{ minHeight: `${BAR_HEIGHT + 30}px` }">
            <div v-for="m in monthlyPnl" :key="m.month"
                 class="flex flex-col items-center shrink-0" style="min-width: 56px;">
              <!-- Bar centered on zero line -->
              <div class="relative flex flex-col items-center justify-center"
                   :style="{ height: `${BAR_HEIGHT}px`, width: '32px' }">
                <!-- Zero line -->
                <div class="absolute left-0 right-0 border-t border-bg-300"
                     :style="{ top: `${BAR_HEIGHT / 2}px` }"></div>
                <!-- Positive bar -->
                <div v-if="m.pnl > 0"
                     class="absolute bottom-1/2 left-1/2 -translate-x-1/2 bg-signal-400/60 hover:bg-signal-400 transition"
                     :style="{ height: `${barY(m.pnl)}px`, width: '24px' }"
                     :title="`${m.picks} picks, ${m.wins}W, P&L ${fmtMoney(m.pnl)}`"></div>
                <!-- Negative bar -->
                <div v-if="m.pnl < 0"
                     class="absolute top-1/2 left-1/2 -translate-x-1/2 bg-edge-cold-1/60 hover:bg-edge-cold-1 transition"
                     :style="{ height: `${barY(m.pnl)}px`, width: '24px' }"
                     :title="`${m.picks} picks, ${m.wins}W, P&L ${fmtMoney(m.pnl)}`"></div>
              </div>
              <div class="text-[9px] text-fg-500 mt-1 font-mono whitespace-nowrap">{{ fmtMonth(m.month) }}</div>
              <div class="display-num text-[10px] mt-0.5"
                   :class="m.pnl >= 0 ? 'text-signal-400' : 'text-edge-cold-1'">
                {{ fmtMoney(m.pnl) }}
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
