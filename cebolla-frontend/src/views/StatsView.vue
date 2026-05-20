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
 * Source: pods table. Reads only settled rows (status IN win/loss/push/void).
 * Everything is computed client-side — no backend math.
 */

import { ref, computed, onMounted } from 'vue'
import { supabase } from '../supabase.js'
import LoadingBrand from '../components/LoadingBrand.vue'

const pods = ref([])
const loading = ref(true)
const error = ref(null)

// ── Load ──────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    const { data, error: dbErr } = await supabase
      .from('pods')
      .select('id, pod_date, market_class, market, projected_prob, no_vig_prob, ' +
              'edge, american_odds, status, payout, stake, contact_score, combined_score, ' +
              'player_name, team_abbrev, opponent_abbrev, settled_at')
      .order('pod_date', { ascending: true })
      .limit(2000)
    if (dbErr) throw dbErr
    pods.value = data || []
  } catch (e) {
    console.error('[StatsView] load failed:', e)
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}
onMounted(load)

// ── Derived: settled pods ─────────────────────────────────────
const settled = computed(() =>
  pods.value.filter(p => ['win', 'loss', 'push', 'void'].includes(p.status))
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
function marketSlice(mc) {
  return settled.value.filter(p => (p.market_class || 'hr') === mc)
}
const hrStats = computed(() => {
  const list = marketSlice('hr')
  return {
    record: recordFor(list),
    pnl: pnlFor(list),
    roi: roiFor(list),
    hitRate: hitRateFor(list),
  }
})
const hrrStats = computed(() => {
  const list = marketSlice('hrr')
  return {
    record: recordFor(list),
    pnl: pnlFor(list),
    roi: roiFor(list),
    hitRate: hitRateFor(list),
  }
})

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

// ── Per-odds-tier breakdown ───────────────────────────────────
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
  for (const pod of settled.value) {
    const dt = pod.settled_at || pod.pod_date
    if (!dt) continue
    const key = String(dt).slice(0, 7) // YYYY-MM
    if (!byMonth.has(key)) {
      byMonth.set(key, { month: key, picks: 0, wins: 0, pnl: 0 })
    }
    const bucket = byMonth.get(key)
    bucket.picks++
    if (pod.status === 'win') bucket.wins++
    bucket.pnl += Number(pod.payout) || 0
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
