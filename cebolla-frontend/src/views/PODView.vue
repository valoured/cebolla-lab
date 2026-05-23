<script setup>
/**
 * PODView.vue — Cebolla's Play of the Day scoreboard.
 *
 * What this is:
 *   Every day, the model algorithmically picks ONE bet — the HR prop with
 *   the highest combined score (normalized edge × normalized contact), gated
 *   to picks with projected_prob >= 20%. That pick is locked BEFORE first
 *   pitch (cron at 10:13 AM ET via pick_pod.py). After games finish, it's
 *   settled. The cumulative P&L of every settled POD is the public scoreboard
 *   for "is this model actually good."
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

import { ref, computed, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { playerHeadshotUrl, teamLogoUrl, hideOnError } from '../utils/mlbImages.js'
import LoadingBrand from '../components/LoadingBrand.vue'

const pods = ref([])           // all PODs, newest first
const todayGames = ref([])     // today's games keyed for status lookup
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

// Helper: ET-relative today (DST-safe via Intl.DateTimeFormat — handles
// EDT/EST transitions automatically). Used by load() and derived constants.
function todayIsoFn() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
}

// ── Load ───────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    // Parallel: PODs history (limit 200) AND today's game statuses.
    // Both are independent queries — no need to wait sequentially.
    // Previously this was two sequential round-trips (~300ms on mobile);
    // now ~150ms with Promise.all.
    const today = todayIsoFn()
    const [podsRes, gamesRes] = await Promise.all([
      supabase
        .from('pods')
        .select('*')
        .order('pod_date', { ascending: false })
        .limit(200),
      supabase
        .from('games')
        .select('id, status, game_time_utc')
        .eq('game_date', today),
    ])

    if (podsRes.error) throw podsRes.error
    pods.value = podsRes.data || []
    todayGames.value = gamesRes.data || []

    loading.value = false
  } catch (e) {
    console.error('[PODView] load failed:', e)
    error.value = e.message || String(e)
    loading.value = false
  }
}

// Lightweight refresh: ONLY fetch today's game statuses + today's POD rows
// (status flips when settle_pods grades them). Avoids re-pulling 200 historical
// PODs every minute. Called by the auto-refresh interval and on tab focus.
async function refreshLive() {
  try {
    const today = todayIsoFn()

    // Parallel: both queries are independent. ~150ms saved per refresh
    // (which fires every 60s).
    const [gamesRes, todayPodsRes] = await Promise.all([
      // Today's game statuses (mainly for STARTING SOON → LIVE flip)
      supabase
        .from('games')
        .select('id, status, game_time_utc')
        .eq('game_date', today),
      // Today's PODs only (status flips when settle grades them)
      supabase
        .from('pods')
        .select('*')
        .eq('pod_date', today),
    ])

    if (gamesRes.data) todayGames.value = gamesRes.data

    if (todayPodsRes.data) {
      // Replace today's rows in pods.value; keep historical untouched.
      // Update in-place so we don't disturb the historical list.
      const todayIds = new Set(todayPodsRes.data.map(p => p.id))
      const historical = pods.value.filter(p => !todayIds.has(p.id) && p.pod_date !== today)
      pods.value = [...todayPodsRes.data, ...historical]
    }
  } catch (e) {
    // Swallow — auto-refresh failures shouldn't break the page.
    // Initial load() failure is what surfaces errors to the user.
    console.warn('[PODView] refresh failed (silent):', e)
  }
}

// Auto-refresh: every 60s while the page is open AND visible. Also refresh
// immediately when user re-focuses the tab (more responsive than waiting up
// to a full minute). Cleared on unmount to prevent leaked intervals.
let refreshTimer = null

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer)
  refreshTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
      return  // skip while tab is in background — refresh on next focus instead
    }
    dateTick.value++  // keep `todayIso` reactive past midnight ET
    refreshLive()
  }, 60_000)
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible') {
    refreshLive()  // immediate refresh on tab focus
  }
}

onMounted(() => {
  load()
  startAutoRefresh()
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }
})

onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }
})

// ── Derived ────────────────────────────────────────────────────
// Reactive today-in-ET. Bumped once per minute by the refresh interval
// so the value stays correct past the midnight boundary. Without this,
// post-midnight cards would silently fall into "historical" classification.
const dateTick = ref(0)
const todayIso = computed(() => {
  dateTick.value  // reactive dep
  return todayIsoFn()
})

// Hour of the day in ET (0-23). Used to switch the empty-state copy:
// before the morning lock window we show "check back after morning lock",
// after it we show "no batter cleared thresholds today" since pick_pod
// has already run and either failed to find candidates or skipped due to
// insufficient projections.
//
// Reactive via dateTick — bumped every minute by the auto-refresh
// interval so a user who keeps the tab open through the 11 AM ET boundary
// sees the empty-state copy flip from "check back" to "no batter cleared
// thresholds today" without having to refresh the page.
function currentETHour() {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric',
    hour12: false,
  })
  return parseInt(fmt.format(new Date()), 10)
}
const pastMorningLock = computed(() => {
  dateTick.value  // reactive dep — re-evaluate on each minute tick
  return currentETHour() >= 11   // 11 AM ET — pick_pod fires by ~3:43 AM, but allow buffer
})

const todayPod = computed(() => pods.value.find(p => p.pod_date === todayIso.value) || null)
const historicalPods = computed(() => pods.value.filter(p => p.pod_date !== todayIso.value))
const settledPods = computed(() => pods.value.filter(p => ['win', 'loss', 'push'].includes(p.status)))

// ── Per-market_class filtering (dual POD: HR + HRR) ────────────────────
// All POD rows carry a `market_class` tag set at lock time. Until the HRR
// model ships, all existing rows are 'hr' (backfilled by the migration).
// These helpers let the template show two separate cards/scoreboards side-
// by-side, one per market, without re-querying the DB.
//
// Today's POD per market is computed (not a function) because the template
// references it ~20 times per render across both market sections. Functions
// would re-filter pods.value on every reference; computed caches the result
// until pods.value changes.
const todayPodHr = computed(() => pods.value.find(
  p => p.pod_date === todayIso.value && (p.market_class || 'hr') === 'hr',
) || null)

const todayPodHrr = computed(() => pods.value.find(
  p => p.pod_date === todayIso.value && (p.market_class || 'hr') === 'hrr',
) || null)

// Helpers for per-market filtering/stats. The overall scoreboard uses the
// global computeds (record, netPnl, roi). Per-market scoreboard sections
// aren't built into the UI yet — these helpers are wired up so they can be
// dropped in without re-plumbing the data layer.
function podsByMarket(marketClass) {
  return pods.value.filter(p => (p.market_class || 'hr') === marketClass)
}
function todayPodForMarket(marketClass) {
  return marketClass === 'hr' ? todayPodHr.value : todayPodHrr.value
}
function historicalPodsForMarket(marketClass) {
  return pods.value.filter(
    p => p.pod_date !== todayIso.value && (p.market_class || 'hr') === marketClass,
  )
}
function settledPodsForMarket(marketClass) {
  return pods.value.filter(
    p => ['win', 'loss', 'push'].includes(p.status) &&
         (p.market_class || 'hr') === marketClass,
  )
}

// Scale a stored payout (at canonical stake) up/down to viewer's stake.
function scaledPayout(pod) {
  const canon = Number(pod.stake) || 10
  const factor = displayStake.value / canon
  return Number(pod.payout || 0) * factor
}

// Win/loss record — overall (kept for backward compat) and per-market.
const record = computed(() => recordFor(settledPods.value))

function recordFor(podList) {
  let w = 0, l = 0, p = 0, v = 0
  for (const pod of podList) {
    if (pod.status === 'win') w++
    else if (pod.status === 'loss') l++
    else if (pod.status === 'push') p++
    else if (pod.status === 'void') v++
  }
  return { w, l, p, v, settled: w + l + p }
}

function recordForMarket(marketClass) {
  return recordFor(settledPodsForMarket(marketClass))
}

// Net P&L — overall + per-market
const netPnl = computed(() => pnlFor(settledPods.value))

function pnlFor(podList) {
  let total = 0
  for (const pod of podList) total += scaledPayout(pod)
  return total
}

function pnlForMarket(marketClass) {
  return pnlFor(settledPodsForMarket(marketClass))
}

// ROI — overall + per-market
const roi = computed(() => roiFor(settledPods.value))

function roiFor(podList) {
  const risked = podList
    .filter(p => p.status === 'win' || p.status === 'loss')
    .length * displayStake.value
  if (risked === 0) return null
  return pnlFor(podList) / risked
}

function roiForMarket(marketClass) {
  return roiFor(settledPodsForMarket(marketClass))
}

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
// Format an ISO timestamp as time-of-day in ET. The "ET" label next to
// these in the template would be misleading otherwise — without an explicit
// timezone, toLocaleTimeString uses the user's local TZ.
function fmtLockedAtET(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
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

// Look up the full game object (status + start time) for a POD's game.
// Returns null if the game isn't in our cached set.
function gameFor(pod) {
  if (!pod || !pod.game_id) return null
  return todayGames.value.find(x => x.id === pod.game_id) || null
}

function gameStatusFor(pod) {
  const g = gameFor(pod)
  return g ? (g.status || null) : null
}

// True if the game has started based on its scheduled start time.
// 5-min buffer absorbs late starts / clock skew.
function gameHasStartedByTime(pod) {
  const g = gameFor(pod)
  if (!g || !g.game_time_utc) return false
  const startMs = new Date(g.game_time_utc).getTime()
  if (Number.isNaN(startMs)) return false
  return Date.now() > startMs + 5 * 60 * 1000
}

// True if the game's status string indicates a settled/over state.
function gameStatusIsFinal(gameStatus) {
  if (!gameStatus) return false
  const s = String(gameStatus).toLowerCase()
  return s === 'final' || s.includes('game over') || s.includes('completed early')
}

// True if game status string indicates pre-game (Scheduled / Warmup / etc).
function gameStatusIsPregame(gameStatus) {
  if (!gameStatus) return true
  const s = String(gameStatus).toLowerCase()
  if (s.includes('scheduled')) return true
  if (s.includes('pre-game') || s.includes('pre game') || s.includes('pregame')) return true
  if (s.includes('warmup')) return true
  if (s.includes('delayed start')) return true
  return false
}

// True if the game is currently live.
// Time-based primary: once we're past first pitch + 5 min, it's LIVE even
// if pull_scores hasn't updated the status string yet (cron lags up to ~1 hr).
// Status-based fallback: catches statuses like "In Progress" / "Manager
// Challenge" / "Delayed" that pull_scores has already written.
function gameIsLive(pod) {
  const status = gameStatusFor(pod)
  // If the DB knows it's final, trust that and don't show LIVE.
  if (gameStatusIsFinal(status)) return false
  // If we're past scheduled first pitch, treat as live regardless of stale status.
  if (gameHasStartedByTime(pod)) return true
  // Status string says it's actively going.
  if (status && !gameStatusIsPregame(status)) return true
  return false
}

// True if the game hasn't started yet.
function gameIsPregame(pod) {
  const status = gameStatusFor(pod)
  if (gameStatusIsFinal(status)) return false
  if (gameHasStartedByTime(pod)) return false   // time says it's already going
  if (status && !gameStatusIsPregame(status)) return false  // status says it's going
  return true
}

// Smart label for a pending POD based on its game's live state.
//   game not started → STARTING SOON
//   game live        → LIVE
//   game final but pod still pending → PENDING (settle job hasn't run yet)
function pendingLabelFor(pod) {
  if (gameIsPregame(pod)) return 'STARTING SOON'
  if (gameIsLive(pod))    return 'LIVE'
  return 'PENDING'
}

// Smart badge class for pending PODs — different visual treatment for LIVE.
function pendingBadgeClass(pod) {
  if (gameIsLive(pod)) return 'badge-live'
  return 'badge-pending'
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
            Projection of the Day
          </h1>
          <span class="label-bracket text-signal-400">M.02</span>
        </div>
        <p class="text-fg-500 text-xs mt-2 max-w-2xl">
          Each morning, Cebolla's model identifies one high-confidence HR projection — the
          player with the highest combined edge × contact score, gated to projections with at
          least a 20% projected probability. Generated before first pitch and graded after
          games end. Public, transparent record. For informational and entertainment purposes
          only — not betting advice.
        </p>
      </header>

      <LoadingBrand v-if="loading" text="Loading PODs…" />

      <div v-else-if="error" class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">load failed</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>

      <template v-else>
        <!-- ── TODAY'S PODS (per market_class) ────────────────── -->
        <!-- HR POD (live since launch) -->
        <section class="mb-4">
          <div class="flex items-baseline justify-between mb-2 flex-wrap gap-2">
            <div class="flex items-baseline gap-2">
              <h2 class="label-bracket text-signal-400">today · hr · {{ fmtDate(todayIso) }}</h2>
              <span class="label-caps !text-[8px] text-fg-500">anytime home run</span>
            </div>
            <span
              v-if="todayPodForMarket('hr')"
              class="badge"
              :class="todayPodForMarket('hr').status === 'pending'
                ? pendingBadgeClass(todayPodForMarket('hr'))
                : statusBadgeClass(todayPodForMarket('hr').status)"
            >
              {{ todayPodForMarket('hr').status === 'pending'
                ? pendingLabelFor(todayPodForMarket('hr'))
                : statusLabel(todayPodForMarket('hr').status) }}
            </span>
          </div>

          <!-- Locked HR pick -->
          <div v-if="todayPodForMarket('hr')" class="pod-card">
            <div class="flex items-center gap-3 sm:gap-4 mb-3">
              <img
                v-if="todayPodForMarket('hr').player_mlbam_id"
                :src="playerHeadshotUrl(todayPodForMarket('hr').player_mlbam_id)"
                :alt="todayPodForMarket('hr').player_name"
                class="pod-headshot"
                @error="hideOnError"
              />
              <div v-else class="pod-headshot pod-headshot--fallback"></div>
              <div class="flex-1 min-w-0">
                <div class="display-text text-xl sm:text-2xl text-fg-800 leading-tight truncate">
                  {{ todayPodForMarket('hr').player_name }}
                </div>
                <div class="flex items-baseline gap-2 mt-1 flex-wrap">
                  <span class="label-bracket text-signal-400">{{ todayPodForMarket('hr').team_abbrev }}</span>
                  <span class="text-fg-500 text-xs italic">vs</span>
                  <span class="label-bracket text-fg-600">{{ todayPodForMarket('hr').opponent_abbrev }}</span>
                  <span class="label-caps !text-[9px]">{{ marketLabel(todayPodForMarket('hr').market) }}</span>
                </div>
              </div>
            </div>

            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-3 border-t border-bg-200">
              <div>
                <div class="label-caps !text-[9px]">Cebolla Prob</div>
                <div class="display-num text-xl text-fg-800 mt-1">
                  {{ fmtProb(todayPodForMarket('hr').projected_prob) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">Odds</div>
                <div class="display-num text-xl text-fg-800 mt-1">
                  {{ fmtOdds(todayPodForMarket('hr').american_odds) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">Edge</div>
                <div
                  class="display-num text-xl mt-1"
                  :class="todayPodForMarket('hr').edge > 0 ? 'text-signal-400' : 'text-fg-600'"
                >
                  {{ fmtPct(todayPodForMarket('hr').edge) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">If hit at ${{ displayStake }}</div>
                <div class="display-num text-xl text-signal-400 mt-1">
                  +{{ fmtMoney(displayStake * (todayPodForMarket('hr').american_odds >= 0 ? todayPodForMarket('hr').american_odds / 100 : 100 / Math.abs(todayPodForMarket('hr').american_odds)), false).replace('$', '$') }}
                </div>
              </div>
            </div>

            <div
              v-if="todayPodForMarket('hr').book"
              class="mt-3 pt-3 border-t border-bg-200/40 label-caps !text-[8px] opacity-70"
            >
              odds from {{ todayPodForMarket('hr').book }} · locked
              {{ fmtLockedAtET(todayPodForMarket('hr').locked_at) }} ET
            </div>
          </div>

          <!-- No HR POD yet -->
          <div v-else class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
            <template v-if="!pastMorningLock">
              <div class="display-text text-lg text-fg-500 italic mb-1">No projection locked yet</div>
              <p class="text-fg-500 text-xs">
                Cebolla locks the day's HR POD by ~10:30 AM ET. Check back after morning projections run.
              </p>
            </template>
            <template v-else>
              <div class="display-text text-lg text-fg-500 italic mb-1">No HR POD today</div>
              <p class="text-fg-500 text-xs max-w-md mx-auto">
                The model didn't find a batter who cleared today's confidence thresholds &mdash; combined projected probability + edge floors weren't met by any candidate on this slate.
              </p>
              <p class="text-fg-400 text-[10px] mt-2 italic">
                Tomorrow's slate offers fresh candidates. The HRR POD is still live below.
              </p>
            </template>
          </div>
        </section>

        <!-- HRR POD (live) -->
        <section class="mb-6">
          <div class="flex items-baseline justify-between mb-2 flex-wrap gap-2">
            <div class="flex items-baseline gap-2">
              <h2 class="label-bracket text-signal-400">today · hrr · {{ fmtDate(todayIso) }}</h2>
              <span class="label-caps !text-[8px] text-fg-500">hits + runs + rbis</span>
            </div>
            <span
              v-if="todayPodForMarket('hrr')"
              class="badge"
              :class="todayPodForMarket('hrr').status === 'pending'
                ? pendingBadgeClass(todayPodForMarket('hrr'))
                : statusBadgeClass(todayPodForMarket('hrr').status)"
            >
              {{ todayPodForMarket('hrr').status === 'pending'
                ? pendingLabelFor(todayPodForMarket('hrr'))
                : statusLabel(todayPodForMarket('hrr').status) }}
            </span>
          </div>

          <div
            v-if="todayPodForMarket('hrr')"
            class="pod-card"
          >
            <!-- Once HRR PODs start being picked, render the same card structure
                 as the HR side. For brevity I'm reusing the same template
                 shape inline. -->
            <div class="flex items-center gap-3 sm:gap-4 mb-3">
              <img
                v-if="todayPodForMarket('hrr').player_mlbam_id"
                :src="playerHeadshotUrl(todayPodForMarket('hrr').player_mlbam_id)"
                :alt="todayPodForMarket('hrr').player_name"
                class="pod-headshot"
                @error="hideOnError"
              />
              <div v-else class="pod-headshot pod-headshot--fallback"></div>
              <div class="flex-1 min-w-0">
                <div class="display-text text-xl sm:text-2xl text-fg-800 leading-tight truncate">
                  {{ todayPodForMarket('hrr').player_name }}
                </div>
                <div class="flex items-baseline gap-2 mt-1 flex-wrap">
                  <span class="label-bracket text-signal-400">{{ todayPodForMarket('hrr').team_abbrev }}</span>
                  <span class="text-fg-500 text-xs italic">vs</span>
                  <span class="label-bracket text-fg-600">{{ todayPodForMarket('hrr').opponent_abbrev }}</span>
                  <span class="label-caps !text-[9px]">{{ marketLabel(todayPodForMarket('hrr').market) }}</span>
                </div>
              </div>
            </div>

            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-3 border-t border-bg-200">
              <div>
                <div class="label-caps !text-[9px]">Cebolla Prob</div>
                <div class="display-num text-xl text-fg-800 mt-1">
                  {{ fmtProb(todayPodForMarket('hrr').projected_prob) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">Odds</div>
                <div class="display-num text-xl text-fg-800 mt-1">
                  {{ fmtOdds(todayPodForMarket('hrr').american_odds) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">Edge</div>
                <div
                  class="display-num text-xl mt-1"
                  :class="todayPodForMarket('hrr').edge > 0 ? 'text-signal-400' : 'text-fg-600'"
                >
                  {{ fmtPct(todayPodForMarket('hrr').edge) }}
                </div>
              </div>
              <div>
                <div class="label-caps !text-[9px]">If hit at ${{ displayStake }}</div>
                <div class="display-num text-xl text-signal-400 mt-1">
                  +{{ fmtMoney(displayStake * (todayPodForMarket('hrr').american_odds >= 0 ? todayPodForMarket('hrr').american_odds / 100 : 100 / Math.abs(todayPodForMarket('hrr').american_odds)), false).replace('$', '$') }}
                </div>
              </div>
            </div>

            <div
              v-if="todayPodForMarket('hrr').book"
              class="mt-3 pt-3 border-t border-bg-200/40 label-caps !text-[8px] opacity-70"
            >
              odds from {{ todayPodForMarket('hrr').book }} · locked
              {{ fmtLockedAtET(todayPodForMarket('hrr').locked_at) }} ET
            </div>
          </div>

          <!-- No HRR POD locked today -->
          <div v-else class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
            <template v-if="!pastMorningLock">
              <div class="display-text text-lg text-fg-500 italic mb-1">No projection locked yet</div>
              <p class="text-fg-500 text-xs">
                Cebolla locks the day's HRR POD by ~10:30 AM ET. Check back after morning projections run.
              </p>
            </template>
            <template v-else>
              <div class="display-text text-lg text-fg-500 italic mb-1">No HRR POD today</div>
              <p class="text-fg-500 text-xs max-w-md mx-auto">
                The model didn't find a batter who cleared today's confidence thresholds across the H+R+RBI lines. Edge floor and per-line projection floors weren't met by any candidate.
              </p>
              <p class="text-fg-400 text-[10px] mt-2 italic">
                Tomorrow's slate offers fresh candidates.
              </p>
            </template>
          </div>
        </section>

        <!-- ── PERFORMANCE ─────────────────────────────────── -->
        <section v-if="settledPods.length > 0" class="mb-6">
          <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
            <h2 class="label-bracket text-signal-400">performance</h2>
            <!-- Stake adjuster -->
            <div class="flex items-baseline gap-2">
              <span class="label-caps !text-[9px]">hypothetical $10</span>
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
          <h2 class="label-bracket text-signal-400 mb-3">recent projections</h2>
          <div class="bg-bg-50 border border-bg-200 overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left">
                  <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Date</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-center">Mkt</th>
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
                  <td class="py-2 px-2 border-b border-bg-200/40 text-center font-mono text-[9px] text-fg-500">
                    {{ ((pod.market_class || 'hr')).toUpperCase() }}
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
.badge-live {
  color: rgba(80, 220, 130, 0.95);
  background: rgba(80, 220, 130, 0.10);
  border-color: rgba(80, 220, 130, 0.50);
  animation: live-pulse 2.2s ease-in-out infinite;
}
@keyframes live-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.6; }
}
</style>
