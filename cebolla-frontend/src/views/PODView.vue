<script setup>
/**
 * PODView.vue — Cebolla's Play of the Day scoreboard.
 *
 * What this is:
 *   Every day, the model algorithmically picks ONE bet — the highest-edge
 *   HR prop with projected win probability >= 30%. That pick is locked
 *   BEFORE first pitch (cron at 10:13 AM ET via pick_pod.py). After
 *   games finish, it's settled. The cumulative P&L of every settled
 *   POD is the public scoreboard for "is this model actually good."
 *
 * Stake interpretation:
 *   The DB stores a canonical $10 stake for every POD. This page lets
 *   the viewer scale the displayed numbers by adjusting their preferred
 *   stake. The underlying record never changes. The scoreboard is honest;
 *   the displayed dollars are for the viewer's mental math.
 *
 * Sections:
 *   1. Today's POD (or "no pick locked yet")
 *   2. Stake adjuster + lifetime stats (W/L record, ROI, net P&L)
 *   3. Cumulative P&L sparkline over time
 *   4. Recent picks history with W/L badges
 */

import { ref, computed, onMounted } from 'vue'
import { supabase } from '../supabase.js'
import { playerHeadshotUrl, teamLogoUrl, hideOnError } from '../utils/mlbImages.js'
import LoadingBrand from '../components/LoadingBrand.vue'

const pods = ref([])           // all PODs, newest first
const loading = ref(true)
const error = ref(null)

// Viewer's preferred display stake. Stored in localStorage so it persists.
const STAKE_KEY = 'cebolla.pod.stake.v1'
const displayStake = ref(Number(localStorage.getItem(STAKE_KEY)) || 10)

function saveStake(v) {
  const n = Number(v)
  if (Number.isFinite(n) && n > 0 && n <= 100000) {
    displayStake.value = n
    localStorage.setItem(STAKE_KEY, String(n))
  }
}

// ── Load ───────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    const { data, error: dbErr } = await supabase
      .from('pods')
      .select('*')
      .order('pod_date', { ascending: false })
      .limit(200)
    if (dbErr) throw dbErr
    pods.value = data || []
    loading.value = false
  } catch (e) {
    console.error('[PODView] load failed:', e)
    error.value = e.message || String(e)
    loading.value = false
  }
}

onMounted(load)

// ── Derived ────────────────────────────────────────────────────
const todayIso = (() => {
  // ET-relative baseball day — same convention as the picker script.
  const now = new Date()
  const offsetMs = -4 * 60 * 60 * 1000
  const et = new Date(now.getTime() + offsetMs)
  return et.toISOString().slice(0, 10)
})()

const todayPod = computed(() => pods.value.find(p => p.pod_date === todayIso) || null)
const historicalPods = computed(() => pods.value.filter(p => p.pod_date !== todayIso))
const settledPods = computed(() => pods.value.filter(p => ['win', 'loss', 'push'].includes(p.status)))

// Scale a stored payout (at canonical stake) up/down to viewer's stake.
function scaledPayout(pod) {
  const canon = Number(pod.stake) || 10
  const factor = displayStake.value / canon
  return Number(pod.payout || 0) * factor
}

function scaledRisk(pod) {
  // Risk = stake. Always equal to displayStake for the scaled view.
  return displayStake.value
}

// Win/loss record
const record = computed(() => {
  let w = 0, l = 0, p = 0, v = 0
  for (const pod of settledPods.value) {
    if (pod.status === 'win') w++
    else if (pod.status === 'loss') l++
    else if (pod.status === 'push') p++
    else if (pod.status === 'void') v++
  }
  return { w, l, p, v, settled: w + l + p }
})

// Net P&L at displayStake
const netPnl = computed(() => {
  let total = 0
  for (const pod of settledPods.value) {
    total += scaledPayout(pod)
  }
  return total
})

// ROI = net P&L / total risked. Pushes don't count as risk (refunded).
const roi = computed(() => {
  const risked = settledPods.value
    .filter(p => p.status === 'win' || p.status === 'loss')
    .length * displayStake.value
  if (risked === 0) return null
  return netPnl.value / risked
})

// Cumulative P&L over time for the sparkline.
const pnlTimeline = computed(() => {
  // Ascending date order
  const asc = [...settledPods.value].reverse()
  const series = []
  let cum = 0
  for (const pod of asc) {
    cum += scaledPayout(pod)
    series.push({ date: pod.pod_date, cum })
  }
  return series
})

// SVG sparkline coordinates
const sparklineWidth = 600
const sparklineHeight = 100
const sparklinePadding = 6

const sparklinePath = computed(() => {
  const series = pnlTimeline.value
  if (series.length < 2) return ''
  const xs = series.map((_, i) => i)
  const ys = series.map(d => d.cum)
  const minY = Math.min(0, ...ys)  // include 0 so baseline is visible
  const maxY = Math.max(0, ...ys)
  const rangeY = maxY - minY || 1
  const w = sparklineWidth - sparklinePadding * 2
  const h = sparklineHeight - sparklinePadding * 2
  const stepX = w / (series.length - 1 || 1)

  const pts = series.map((d, i) => {
    const x = sparklinePadding + i * stepX
    const y = sparklinePadding + h - ((d.cum - minY) / rangeY) * h
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  return 'M ' + pts.join(' L ')
})

const sparklineZeroY = computed(() => {
  const series = pnlTimeline.value
  if (series.length === 0) return sparklineHeight / 2
  const ys = series.map(d => d.cum)
  const minY = Math.min(0, ...ys)
  const maxY = Math.max(0, ...ys)
  const rangeY = maxY - minY || 1
  const h = sparklineHeight - sparklinePadding * 2
  return sparklinePadding + h - ((0 - minY) / rangeY) * h
})

const isProfit = computed(() => netPnl.value >= 0)

// ── Formatting ────────────────────────────────────────────────
function fmtOdds(o) {
  if (o == null) return '—'
  return o > 0 ? `+${o}` : String(o)
}
function fmtMoney(v, withSign = true) {
  if (v == null) return '—'
  const sign = withSign && v > 0 ? '+' : ''
  return `${sign}$${Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2)}`
}
function fmtPct(v) {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}
function fmtProb(v) {
  if (v == null) return '—'
  return `${(Number(v) * 100).toFixed(1)}%`
}
function fmtDate(iso) {
  if (!iso) return '—'
  const [y, m, d] = iso.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
function statusBadgeClass(status) {
  switch (status) {
    case 'win':     return 'badge-win'
    case 'loss':    return 'badge-loss'
    case 'push':    return 'badge-push'
    case 'void':    return 'badge-void'
    case 'pending': return 'badge-pending'
    default:        return 'badge-pending'
  }
}
function statusLabel(status) {
  switch (status) {
    case 'win':     return 'WIN'
    case 'loss':    return 'LOSS'
    case 'push':    return 'PUSH'
    case 'void':    return 'VOID'
    case 'pending': return 'PENDING'
    default:        return (status || '?').toUpperCase()
  }
}
function marketLabel(m) {
  if (m === 'hr_0.5') return 'To Hit a HR'
  return m
}
</script>

<template>
  <div class="min-h-screen">
    <div class="max-w-4xl mx-auto px-4 sm:px-6 py-5">

      <!-- Header -->
      <header class="mb-5">
        <div class="flex items-baseline gap-3 flex-wrap">
          <h1 class="display-text text-2xl sm:text-3xl text-fg-800 tracking-tight leading-none">
            Play of the Day
          </h1>
          <span class="label-bracket text-signal-400">M.02</span>
        </div>
        <p class="text-fg-500 text-xs mt-2 max-w-2xl">
          Every morning, Cebolla automatically picks one HR prop — the highest-edge bet
          with a projected win probability of at least 30%. Locked in before first pitch,
          settled after games end. This is the public scoreboard.
        </p>
      </header>

      <LoadingBrand v-if="loading" text="Loading PODs…" />

      <div v-else-if="error" class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">load failed</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>

      <template v-else>
        <!-- ── TODAY'S POD ─────────────────────────────────── -->
        <section class="mb-6">
          <div class="flex items-baseline justify-between mb-2">
            <h2 class="label-bracket text-signal-400">today · {{ fmtDate(todayIso) }}</h2>
            <span v-if="todayPod" class="badge" :class="statusBadgeClass(todayPod.status)">
              {{ statusLabel(todayPod.status) }}
            </span>
          </div>

          <!-- Locked pick -->
          <div v-if="todayPod" class="pod-card">
            <div class="flex items-center gap-3 sm:gap-4 mb-3">
              <img
                v-if="todayPod.player_mlbam_id"
                :src="playerHeadshotUrl(todayPod.player_mlbam_id)"
                :alt="todayPod.player_name"
                class="pod-headshot"
                @error="hideOnError"
              />
              <div v-else class="pod-headshot pod-headshot--fallback"></div>
              <div class="flex-1 min-w-0">
                <div class="display-text text-xl sm:text-2xl text-fg-800 leading-tight truncate">
                  {{ todayPod.player_name }}
                </div>
                <div class="flex items-baseline gap-2 mt-1 flex-wrap">
                  <span class="label-bracket text-signal-400">{{ todayPod.team_abbrev }}</span>
                  <span class="text-fg-500 text-xs italic">vs</span>
                  <span class="label-bracket text-fg-600">{{ todayPod.opponent_abbrev }}</span>
                  <span class="label-caps !text-[9px]">{{ marketLabel(todayPod.market) }}</span>
                </div>
              </div>
            </div>

            <!-- Stats grid -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-3 border-t border-bg-200">
              <div>
                <div class="label-caps !text-[9px]">Cebolla Prob</div>
                <div class="display-num text-xl text-fg-800 mt-1">
                  {{ fmtProb(todayPod.projected_prob) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">Odds</div>
                <div class="display-num text-xl text-fg-800 mt-1">
                  {{ fmtOdds(todayPod.american_odds) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">Edge</div>
                <div class="display-num text-xl mt-1" :class="todayPod.edge > 0 ? 'text-signal-400' : 'text-fg-600'">
                  {{ fmtPct(todayPod.edge) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">If hit at ${{ displayStake }}</div>
                <div class="display-num text-xl text-signal-400 mt-1">
                  +{{ fmtMoney(displayStake * (todayPod.american_odds >= 0 ? todayPod.american_odds / 100 : 100 / Math.abs(todayPod.american_odds)), false).replace('$', '$') }}
                </div>
              </div>
            </div>

            <div v-if="todayPod.book" class="mt-3 pt-3 border-t border-bg-200/40 label-caps !text-[8px] opacity-70">
              odds from {{ todayPod.book }} · locked {{ new Date(todayPod.locked_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true }) }} ET
            </div>
          </div>

          <!-- No POD yet -->
          <div v-else class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
            <div class="display-text text-lg text-fg-500 italic mb-1">No pick locked yet</div>
            <p class="text-fg-500 text-xs">
              Cebolla locks the day's POD by ~10:30 AM ET. Check back after morning projections run.
            </p>
          </div>
        </section>

        <!-- ── PERFORMANCE ─────────────────────────────────── -->
        <section v-if="settledPods.length > 0" class="mb-6">
          <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
            <h2 class="label-bracket text-signal-400">performance</h2>
            <!-- Stake adjuster -->
            <div class="flex items-baseline gap-2">
              <span class="label-caps !text-[9px]">if i bet</span>
              <span class="text-fg-500 text-sm">$</span>
              <input
                type="number"
                :value="displayStake"
                @input="saveStake($event.target.value)"
                min="1"
                step="1"
                class="stake-input display-num"
              />
              <span class="label-caps !text-[9px]">per pod</span>
            </div>
          </div>

          <!-- Stats row -->
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps !text-[9px]">Record</div>
              <div class="display-num text-xl text-fg-800 mt-1">
                {{ record.w }}-{{ record.l }}<span v-if="record.p" class="text-fg-500 text-xs">-{{ record.p }}</span>
              </div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps !text-[9px]">Net P&L</div>
              <div class="display-num text-xl mt-1" :class="isProfit ? 'text-signal-400' : 'text-edge-cold-1'">
                {{ fmtMoney(netPnl) }}
              </div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps !text-[9px]">ROI</div>
              <div class="display-num text-xl mt-1" :class="roi == null ? 'text-fg-500' : (roi >= 0 ? 'text-signal-400' : 'text-edge-cold-1')">
                {{ roi != null ? (roi >= 0 ? '+' : '') + fmtPct(roi) : '—' }}
              </div>
            </div>
            <div class="bg-bg-50 border border-bg-200 px-3 py-2">
              <div class="label-caps !text-[9px]">Settled</div>
              <div class="display-num text-xl text-fg-800 mt-1">{{ record.settled }}</div>
            </div>
          </div>

          <!-- Cumulative P&L sparkline -->
          <div v-if="pnlTimeline.length >= 2" class="bg-bg-50 border border-bg-200 px-3 py-3">
            <div class="label-caps !text-[8px] mb-2 opacity-70">cumulative p&l · per POD settled</div>
            <svg
              :viewBox="`0 0 ${sparklineWidth} ${sparklineHeight}`"
              preserveAspectRatio="none"
              class="w-full"
              :style="{ height: '100px' }"
            >
              <!-- Zero baseline -->
              <line
                :x1="sparklinePadding"
                :x2="sparklineWidth - sparklinePadding"
                :y1="sparklineZeroY"
                :y2="sparklineZeroY"
                stroke="rgba(255,255,255,0.12)"
                stroke-width="1"
                stroke-dasharray="2 3"
              />
              <!-- P&L line -->
              <path
                :d="sparklinePath"
                fill="none"
                :stroke="isProfit ? '#FF2A2A' : 'rgba(95, 165, 255, 0.85)'"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
            </svg>
          </div>
        </section>

        <!-- ── RECENT PICKS ────────────────────────────────── -->
        <section v-if="historicalPods.length > 0" class="mb-8">
          <h2 class="label-bracket text-signal-400 mb-3">recent picks</h2>
          <div class="bg-bg-50 border border-bg-200 overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left">
                  <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Date</th>
                  <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Player</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Prob</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Odds</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-center">Result</th>
                  <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200 text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="pod in historicalPods"
                  :key="pod.id"
                  class="group hover:bg-bg-100/40 transition"
                >
                  <td class="py-2 px-3 border-b border-bg-200/40 display-num text-xs text-fg-500">
                    {{ fmtDate(pod.pod_date) }}
                  </td>
                  <td class="py-2 px-3 border-b border-bg-200/40">
                    <router-link
                      :to="{ name: 'player', params: { playerId: pod.player_id } }"
                      class="text-fg-700 text-sm group-hover:text-signal-200 transition"
                    >
                      {{ pod.player_name }}
                    </router-link>
                    <span class="font-mono text-[9px] text-fg-500 ml-1.5">
                      {{ pod.team_abbrev }} vs {{ pod.opponent_abbrev }}
                    </span>
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ fmtProb(pod.projected_prob) }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ fmtOdds(pod.american_odds) }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-center">
                    <span class="badge !text-[9px]" :class="statusBadgeClass(pod.status)">
                      {{ statusLabel(pod.status) }}
                    </span>
                  </td>
                  <td class="py-2 px-3 border-b border-bg-200/40 text-right display-num text-xs"
                      :class="pod.status === 'win' ? 'text-signal-400' : (pod.status === 'loss' ? 'text-edge-cold-1' : 'text-fg-500')">
                    <template v-if="pod.status === 'pending'">—</template>
                    <template v-else>{{ fmtMoney(scaledPayout(pod)) }}</template>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- Empty state when there are no settled PODs yet -->
        <section v-else-if="!todayPod" class="mb-8">
          <div class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
            <div class="display-text text-lg text-fg-500 italic mb-1">No PODs yet</div>
            <p class="text-fg-500 text-xs">
              The scoreboard fills in as Cebolla picks and settles each day.
            </p>
          </div>
        </section>
      </template>

    </div>
  </div>
</template>

<style scoped>
.pod-card {
  background: linear-gradient(180deg, rgba(255, 42, 42, 0.04) 0%, rgba(255, 255, 255, 0.02) 100%);
  border: 1px solid rgba(255, 42, 42, 0.20);
  padding: 16px;
}

.pod-headshot {
  width: 56px;
  height: 56px;
  object-fit: cover;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
}
@media (min-width: 640px) {
  .pod-headshot {
    width: 72px;
    height: 72px;
  }
}

.pod-headshot--fallback {
  background: rgba(255, 42, 42, 0.05);
  border: 1px dashed rgba(255, 42, 42, 0.30);
}

/* Stake input — looks like the display nums but is editable */
.stake-input {
  width: 60px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.85);
  padding: 4px 6px;
  font-size: 14px;
  text-align: right;
  font-family: 'JetBrains Mono', monospace;
}
.stake-input:focus {
  outline: none;
  border-color: rgba(255, 42, 42, 0.45);
  background: rgba(255, 42, 42, 0.05);
}
/* Remove Chrome/Firefox number spinners */
.stake-input::-webkit-outer-spin-button,
.stake-input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.stake-input { -moz-appearance: textfield; }

/* Status badges */
.badge {
  display: inline-flex;
  align-items: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.06em;
  padding: 2px 6px;
  line-height: 1;
  border: 1px solid currentColor;
}
.badge-win {
  color: rgba(255, 42, 42, 1);
  background: rgba(255, 42, 42, 0.10);
}
.badge-loss {
  color: rgba(95, 165, 255, 0.95);
  background: rgba(95, 165, 255, 0.08);
}
.badge-push {
  color: rgba(255, 255, 255, 0.65);
  background: rgba(255, 255, 255, 0.04);
}
.badge-void {
  color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.02);
}
.badge-pending {
  color: rgba(255, 200, 80, 0.85);
  background: rgba(255, 200, 80, 0.08);
  border-color: rgba(255, 200, 80, 0.45);
}
</style>
