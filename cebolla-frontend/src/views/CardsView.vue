<script setup>
/**
 * CardsView.vue — Cebolla Cards (rebuilt for AI-built parlays)
 *
 * Renders cards from the `cards` table with their stacked `card_legs`.
 * Each leg shows:
 *   - Headshot, player name, team vs opponent
 *   - Market label + line + American odds
 *   - Status indicator (pregame / live / hit / busted / void)
 *
 * Each card shows:
 *   - Tier badge + label ("Value Combo", "Lottery Shot", etc.)
 *   - Combined American odds + parlay decimal
 *   - "Stake $X → $Y if hit" payout line
 *   - Overall card status badge
 *
 * Sections:
 *   1. Today's Card Menu — grouped by tier (Straights → 2L → 3L → 4L)
 *      PODs are still rendered as "Straights" for unified display.
 *   2. Card History — all settled cards from past days
 */

import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { supabase } from '../supabase.js'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'
import LoadingBrand from '../components/LoadingBrand.vue'
import CardBlock from '../components/CardBlock.vue'

const router = useRouter()

// State
const cards = ref([])           // all cards (today + history)
const legsByCard = ref({})      // card_id → [legs]
const pods = ref([])            // PODs from `pods` table (straights)
const gamesById = ref({})       // game_id → game (for status lookup)
const loading = ref(true)
const error = ref(null)

// Refresh — keep live status fresh while page is open
let refreshTimer = null

// ── Today's ET date ───────────────────────────────────────────
// Driven by a tick ref so the value stays fresh past the midnight ET
// boundary without requiring a page reload. The refresh interval below
// already bumps `dateTick` once per minute, which is plenty for HH:MM
// or YYYY-MM-DD precision.
function todayIsoFn() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date())
}
const dateTick = ref(0)
const todayIso = computed(() => {
  dateTick.value  // reactive dep
  return todayIsoFn()
})

// ── Load ──────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    // Cards — pull recent (today + last 30 days for history)
    const cutoff = new Date()
    cutoff.setDate(cutoff.getDate() - 30)
    const cutoffIso = cutoff.toISOString().slice(0, 10)

    const { data: cardData, error: ce } = await supabase
      .from('cards')
      .select('id, card_date, tier, label, leg_count, combined_prob, ' +
              'combined_odds, decimal_odds, implied_prob, edge, ev_per_dollar, ' +
              'stake_rec, payout_if_hit, status, payout, settled_at, created_at')
      .gte('card_date', cutoffIso)
      .order('card_date', { ascending: false })
      .order('tier', { ascending: true })
      .order('ev_per_dollar', { ascending: false })
    if (ce) throw ce
    cards.value = cardData || []

    // Legs for all loaded cards
    if (cards.value.length) {
      const cardIds = cards.value.map(c => c.id)
      const { data: legData, error: le } = await supabase
        .from('card_legs')
        .select('*')
        .in('card_id', cardIds)
        .order('card_id', { ascending: true })
        .order('leg_order', { ascending: true })
      if (le) throw le
      const byCard = {}
      for (const leg of (legData || [])) {
        if (!byCard[leg.card_id]) byCard[leg.card_id] = []
        byCard[leg.card_id].push(leg)
      }
      legsByCard.value = byCard
    }

    // PODs (straights) — today + history
    const { data: podData, error: pe } = await supabase
      .from('pods')
      .select('id, pod_date, market_class, market, projected_prob, edge, ' +
              'american_odds, status, payout, stake, player_name, ' +
              'team_abbrev, opponent_abbrev, player_id, player_mlbam_id, game_id')
      .gte('pod_date', cutoffIso)
      .order('pod_date', { ascending: false })
    if (pe) throw pe
    pods.value = podData || []

    // Games (for live/final status on legs)
    const gameIds = new Set()
    for (const card of cards.value) {
      const legs = legsByCard.value[card.id] || []
      for (const leg of legs) {
        if (leg.game_id) gameIds.add(leg.game_id)
      }
    }
    for (const pod of pods.value) {
      if (pod.game_id) gameIds.add(pod.game_id)
    }
    if (gameIds.size) {
      const { data: gameData } = await supabase
        .from('games')
        .select('id, status, game_time_utc')
        .in('id', [...gameIds])
      gamesById.value = Object.fromEntries((gameData || []).map(g => [g.id, g]))
    }
  } catch (e) {
    console.error('[CardsView] load failed:', e)
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  // Refresh every 60s while page is open. Bumping dateTick on each tick
  // keeps `todayIso` reactive past the midnight ET boundary — once it
  // flips, `todaysCards` / `todaysPods` immediately reclassify rows.
  refreshTimer = setInterval(() => {
    dateTick.value++
    load()
  }, 60_000)
})
onUnmounted(() => {
  if (refreshTimer) clearInterval(refreshTimer)
})

// ── Derived: today vs history ─────────────────────────────────
const todaysCards = computed(() =>
  cards.value.filter(c => c.card_date === todayIso.value && c.status === 'pending')
)
const todaysPods = computed(() =>
  pods.value.filter(p => p.pod_date === todayIso.value && p.status === 'pending')
)
// History: ANY settled card (win/loss/void), regardless of date.
// Today's settled cards belong in history immediately so users see results
// as games finalize, not just the next day. Sorted newest first.
const historicalCards = computed(() =>
  cards.value.filter(c => c.status !== 'pending')
)
const historicalPods = computed(() =>
  pods.value.filter(p => p.status !== 'pending')
)

// ── Group today's by tier ─────────────────────────────────────
function cardsByTier(tier) {
  return todaysCards.value.filter(c => c.tier === tier)
}
const twoLegCards   = computed(() => cardsByTier('two_leg'))
const threeLegCards = computed(() => cardsByTier('three_leg'))
const fourLegCards  = computed(() => cardsByTier('four_leg'))

// ── Status helpers ────────────────────────────────────────────
function gameStatusFor(gameId) {
  return gamesById.value[gameId]?.status || null
}
function gameTimeFor(gameId) {
  return gamesById.value[gameId]?.game_time_utc || null
}

// Mirror backend pull_scores classification exactly. MLB uses many status
// strings ("In Progress", "Manager Challenge", "Delayed Start: Rain",
// "Game Over", "Postponed", etc.) — substring keyword match handles them.
// Terminal checked FIRST so "Postponed" doesn't match "delayed" later.
const LIVE_KEYWORDS = [
  'in progress', 'manager challenge', 'umpire review',
  'replay', 'instant replay',
  'delayed', 'suspended',
]
const TERMINAL_KEYWORDS = [
  'final', 'game over', 'completed', 'postponed',
  'cancelled', 'canceled', 'forfeit',
]
const PREGAME_KEYWORDS = [
  'scheduled', 'pre-game', 'pregame', 'warmup', 'status unknown',
]
function classifyGameStatus(rawStatus) {
  const s = (rawStatus || '').toLowerCase().trim()
  if (!s) return 'unknown'
  if (TERMINAL_KEYWORDS.some(k => s.includes(k))) return 'final'
  if (LIVE_KEYWORDS.some(k => s.includes(k)))     return 'live'
  if (PREGAME_KEYWORDS.some(k => s.includes(k)))  return 'pregame'
  return 'unknown'
}

// Is the game live? Uses classifier + time-based override for cases where
// pull_scores hasn't fired yet (the row still says "Scheduled" but game has
// definitely started).
function isGameLive(gameId) {
  const g = gamesById.value[gameId]
  if (!g) return false
  const klass = classifyGameStatus(g.status)
  if (klass === 'live') return true
  // Time-based override for stale 'pregame' / 'unknown'
  if (klass === 'pregame' || klass === 'unknown') {
    if (g.game_time_utc) {
      const start = new Date(g.game_time_utc).getTime()
      const now = Date.now()
      if (now > start + 5 * 60_000 && now < start + 4 * 60 * 60_000) return true
    }
  }
  return false
}
function isGameFinal(gameId) {
  return classifyGameStatus(gameStatusFor(gameId)) === 'final'
}

// Leg status — picks the right indicator class + label.
function legStatusIndicator(leg) {
  // If leg has its own settled status (win/loss/void), use it
  if (leg.status === 'win')  return { kind: 'hit',     label: 'HIT'   }
  if (leg.status === 'loss') return { kind: 'busted',  label: 'MISS'  }
  if (leg.status === 'void') return { kind: 'void',    label: 'VOID'  }
  // Pending → check game state
  if (isGameLive(leg.game_id))  return { kind: 'live',     label: 'LIVE'  }
  if (isGameFinal(leg.game_id)) return { kind: 'awaiting', label: 'GRADING' }
  return { kind: 'pregame', label: 'PREGAME' }
}

// Card-level status badge
function cardStatusBadge(card) {
  if (card.status === 'win')     return { kind: 'win',     label: 'CASHED'  }
  if (card.status === 'loss')    return { kind: 'loss',    label: 'BUSTED'  }
  if (card.status === 'void')    return { kind: 'void',    label: 'VOID'    }
  // Pending — show live if any leg's game is live
  const legs = legsByCard.value[card.id] || []
  if (legs.some(l => isGameLive(l.game_id))) {
    return { kind: 'live', label: 'LIVE' }
  }
  return { kind: 'pending', label: 'PENDING' }
}

// ── Formatters ────────────────────────────────────────────────
function fmtOdds(n) {
  if (n == null) return '—'
  return n > 0 ? `+${n}` : `${n}`
}
function fmtPct(n, digits = 1) {
  if (n == null || !Number.isFinite(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(digits)}%`
}
function fmtMoney(n, signed = false) {
  if (n == null || !Number.isFinite(Number(n))) return '—'
  const v = Number(n)
  const sign = signed ? (v > 0 ? '+' : (v < 0 ? '-' : '')) : (v < 0 ? '-' : '')
  return `${sign}$${Math.abs(v).toFixed(2)}`
}
function fmtDate(s) {
  if (!s) return ''
  const [y, m, d] = s.split('-').map(Number)
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${months[m-1]} ${d}`
}
function marketLabel(market, line) {
  if (market === 'hr_anytime') return 'HR Anytime'
  if (market === 'hits_yes')   return `Hits O${line ?? 0.5}`
  if (market === 'rbi_yes')    return `RBI O${line ?? 0.5}`
  if (market && market.startsWith('h_r_rbi_')) {
    return `H+R+RBI O${market.replace('h_r_rbi_', '')}`
  }
  return market || '?'
}
function marketColorClass(market) {
  if (market === 'hr_anytime')    return 'market-hr'
  if (market === 'hits_yes')      return 'market-hits'
  if (market === 'rbi_yes')       return 'market-rbi'
  if (market === 'h_r_rbi_1.5')   return 'market-hrr-low'
  if (market === 'h_r_rbi_2.5')   return 'market-hrr-mid'
  if (market === 'h_r_rbi_3.5')   return 'market-hrr-high'
  if (market && market.startsWith('h_r_rbi_')) return 'market-hrr-mid'
  return 'market-default'
}
function tierLabel(tier) {
  switch (tier) {
    case 'straight':  return 'STRAIGHT'
    case 'two_leg':   return '2-LEGGER'
    case 'three_leg': return '3-LEGGER'
    case 'four_leg':  return 'LOTTERY'
    default: return tier?.toUpperCase() || '?'
  }
}

// Navigation
function openPlayer(playerId) {
  if (!playerId) return
  router.push({ name: 'player', params: { playerId } })
}
</script>

<template>
  <div class="min-h-screen">
    <!-- HEADER -->
    <section class="px-4 sm:px-6 pt-6 pb-4">
      <div class="flex items-baseline gap-3 mb-2">
        <h1 class="display-text text-2xl sm:text-3xl text-fg-800">Cebolla Cards</h1>
        <span class="label-bracket text-fg-500">M.03</span>
      </div>
      <p class="text-fg-500 text-sm max-w-2xl">
        AI-generated parlay analysis for informational and entertainment purposes. The model
        scans every market for mathematical value combinations, applies correlation penalties
        for same-game / same-team / same-player legs, and identifies the highest-EV
        configurations. Each card shows a hypothetical stake and payout structure — not betting
        advice.
      </p>
    </section>

    <LoadingBrand v-if="loading && cards.length === 0" />

    <div v-else-if="error" class="px-6 py-12 text-edge-cold-1">
      Error: {{ error }}
    </div>

    <template v-else>
      <!-- ── TODAY'S CARD MENU ─────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-8">
        <div class="flex items-baseline gap-3 mb-4">
          <h2 class="display-text text-xl text-fg-800">Today's Menu</h2>
          <span class="label-bracket !text-[8px] text-fg-500">{{ fmtDate(todayIso) }}</span>
        </div>

        <!-- ── STRAIGHTS (PODs) ──────────────────────────────── -->
        <div v-if="todaysPods.length" class="mb-6">
          <div class="tier-header">
            <span class="tier-label">STRAIGHTS</span>
            <span class="tier-sublabel">{{ todaysPods.length }} projection{{ todaysPods.length === 1 ? '' : 's' }} · $10 hypothetical</span>
          </div>
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div v-for="pod in todaysPods" :key="`pod-${pod.id}`" class="card-frame">
              <div class="card-header">
                <span class="card-label">
                  ★ {{ pod.market_class === 'hrr' ? 'H+R+RBI POD' : 'HR POD' }}
                </span>
                <span class="badge" :class="`badge-${cardStatusBadge({status: pod.status, id: 0}).kind}`">
                  {{ cardStatusBadge({status: pod.status, id: 0}).label }}
                </span>
              </div>
              <div class="leg-row" @click="openPlayer(pod.player_id)">
                <img v-if="pod.player_mlbam_id"
                     :src="playerHeadshotUrl(pod.player_mlbam_id)"
                     :alt="pod.player_name"
                     class="leg-headshot"
                     @error="hideOnError" />
                <div class="leg-body">
                  <div class="leg-name">{{ pod.player_name }}</div>
                  <div class="leg-meta">
                    <span class="label-bracket text-signal-400">{{ pod.team_abbrev }}</span>
                    <span class="text-fg-500 italic">vs</span>
                    <span class="label-bracket text-fg-600">{{ pod.opponent_abbrev }}</span>
                    <span class="leg-market" :class="marketColorClass(pod.market)">{{ marketLabel(pod.market) }}</span>
                  </div>
                  <div class="leg-numbers">
                    <span class="leg-proj">{{ fmtPct(pod.projected_prob) }} proj</span>
                    <span class="leg-odds">{{ fmtOdds(pod.american_odds) }}</span>
                    <span class="leg-edge" :class="pod.edge > 0 ? 'text-signal-400' : 'text-fg-500'">
                      {{ pod.edge >= 0 ? '+' : '' }}{{ fmtPct(pod.edge) }} edge
                    </span>
                  </div>
                </div>
                <span class="leg-status" :class="`leg-status-${legStatusIndicator({status: pod.status, game_id: pod.game_id}).kind}`">
                  <span class="status-dot"></span>
                  {{ legStatusIndicator({status: pod.status, game_id: pod.game_id}).label }}
                </span>
              </div>
              <div class="card-footer">
                <span class="text-fg-500">$10 stake</span>
                <span class="text-fg-500">·</span>
                <span v-if="pod.status === 'win'" class="text-signal-400 display-num">
                  cashed {{ fmtMoney(pod.payout, true) }}
                </span>
                <span v-else-if="pod.status === 'loss'" class="text-edge-cold-1 display-num">
                  busted {{ fmtMoney(pod.payout, true) }}
                </span>
                <span v-else class="text-fg-700 display-num">
                  → {{ fmtMoney((10 * (1 + Math.abs(pod.american_odds) / (pod.american_odds < 0 ? Math.abs(pod.american_odds) : 100)))) }} if hit
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- ── 2-LEGGERS ─────────────────────────────────────── -->
        <div v-if="twoLegCards.length" class="mb-6">
          <div class="tier-header">
            <span class="tier-label">2-LEGGERS</span>
            <span class="tier-sublabel">{{ twoLegCards.length }} card{{ twoLegCards.length === 1 ? '' : 's' }} · $10 stake</span>
          </div>
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <CardBlock
              v-for="card in twoLegCards"
              :key="card.id"
              :card="card"
              :legs="legsByCard[card.id] || []"
              :games-by-id="gamesById"
            />
          </div>
        </div>

        <!-- ── 3-LEGGERS ─────────────────────────────────────── -->
        <div v-if="threeLegCards.length" class="mb-6">
          <div class="tier-header">
            <span class="tier-label">3-LEGGERS</span>
            <span class="tier-sublabel">{{ threeLegCards.length }} card{{ threeLegCards.length === 1 ? '' : 's' }} · $5 stake</span>
          </div>
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <CardBlock
              v-for="card in threeLegCards"
              :key="card.id"
              :card="card"
              :legs="legsByCard[card.id] || []"
              :games-by-id="gamesById"
            />
          </div>
        </div>

        <!-- ── LOTTERY ───────────────────────────────────────── -->
        <div v-if="fourLegCards.length" class="mb-6">
          <div class="tier-header">
            <span class="tier-label">LOTTERY</span>
            <span class="tier-sublabel">{{ fourLegCards.length }} ticket · $1 stake</span>
          </div>
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <CardBlock
              v-for="card in fourLegCards"
              :key="card.id"
              :card="card"
              :legs="legsByCard[card.id] || []"
              :games-by-id="gamesById"
            />
          </div>
        </div>

        <!-- Empty -->
        <div v-if="!todaysPods.length && !twoLegCards.length && !threeLegCards.length && !fourLegCards.length"
             class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
          <div class="display-text text-lg text-fg-500 italic mb-1">No cards yet for today</div>
          <p class="text-fg-500 text-xs">
            The card picker runs daily at 2:45 AM ET. Check back after lock time.
          </p>
        </div>
      </section>

      <!-- ── CARD HISTORY ──────────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-10">
        <div class="flex items-baseline gap-3 mb-3">
          <h2 class="display-text text-xl text-fg-800">Card History</h2>
          <span class="label-bracket !text-[8px] text-fg-500">M.03.b</span>
          <span class="text-fg-500 text-xs">·  {{ historicalCards.length + historicalPods.length }} settled</span>
        </div>

        <div v-if="!historicalCards.length && !historicalPods.length"
             class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
          <div class="display-text text-lg text-fg-500 italic mb-1">No settled cards yet</div>
          <p class="text-fg-500 text-xs">
            Once today's picks settle (post-game), the historical ledger populates here.
          </p>
        </div>

        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left border-b border-bg-200">
                <th class="label-caps !text-[8px] py-2 px-3">Date</th>
                <th class="label-caps !text-[8px] py-2 px-3">Card</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">Odds</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-center">Result</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              <!-- Settled Cards -->
              <tr v-for="card in historicalCards" :key="`hc-${card.id}`"
                  class="border-b border-bg-200/40 hover:bg-bg-100/40 transition">
                <td class="py-2 px-3 text-fg-500 font-mono text-[11px]">{{ fmtDate(card.card_date) }}</td>
                <td class="py-2 px-3">
                  <div class="flex items-baseline gap-2">
                    <span class="label-caps !text-[9px] text-fg-500">{{ tierLabel(card.tier) }}</span>
                    <span class="text-fg-700">{{ card.label || tierLabel(card.tier) }}</span>
                  </div>
                  <div class="text-[9px] text-fg-500 font-mono mt-0.5">
                    <span v-for="(leg, idx) in (legsByCard[card.id] || [])" :key="leg.id">
                      <span :class="leg.status === 'win' ? 'text-signal-400' : leg.status === 'loss' ? 'text-edge-cold-1' : 'text-fg-500'">
                        {{ leg.player_name }}
                      </span>
                      <span v-if="idx < (legsByCard[card.id] || []).length - 1" class="text-fg-400"> + </span>
                    </span>
                  </div>
                </td>
                <td class="py-2 px-2 text-right display-num text-signal-200">{{ fmtOdds(card.combined_odds) }}</td>
                <td class="py-2 px-2 text-center">
                  <span class="badge !text-[9px]" :class="`badge-${cardStatusBadge(card).kind}`">
                    {{ cardStatusBadge(card).label }}
                  </span>
                </td>
                <td class="py-2 px-2 text-right display-num"
                    :class="card.status === 'win' ? 'text-signal-400' : (card.status === 'loss' ? 'text-edge-cold-1' : 'text-fg-500')">
                  {{ fmtMoney(card.payout, true) }}
                </td>
              </tr>
              <!-- Settled PODs (legacy) -->
              <tr v-for="pod in historicalPods" :key="`hp-${pod.id}`"
                  class="border-b border-bg-200/40 hover:bg-bg-100/40 transition cursor-pointer"
                  @click="openPlayer(pod.player_id)">
                <td class="py-2 px-3 text-fg-500 font-mono text-[11px]">{{ fmtDate(pod.pod_date) }}</td>
                <td class="py-2 px-3">
                  <div class="flex items-baseline gap-2">
                    <span class="label-caps !text-[9px] text-fg-500">STRAIGHT</span>
                    <span class="text-fg-700">{{ pod.player_name }}</span>
                  </div>
                  <div class="text-[9px] text-fg-500 font-mono mt-0.5">
                    {{ pod.team_abbrev }} vs {{ pod.opponent_abbrev }} · <span class="leg-market" :class="marketColorClass(pod.market)">{{ marketLabel(pod.market) }}</span>
                  </div>
                </td>
                <td class="py-2 px-2 text-right display-num text-signal-200">{{ fmtOdds(pod.american_odds) }}</td>
                <td class="py-2 px-2 text-center">
                  <span class="badge !text-[9px]" :class="`badge-${cardStatusBadge({status: pod.status, id: 0}).kind}`">
                    {{ cardStatusBadge({status: pod.status, id: 0}).label }}
                  </span>
                </td>
                <td class="py-2 px-2 text-right display-num"
                    :class="pod.status === 'win' ? 'text-signal-400' : (pod.status === 'loss' ? 'text-edge-cold-1' : 'text-fg-500')">
                  {{ fmtMoney(pod.payout, true) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
/* ── Tier section headers ─────────────────────────────────── */
.tier-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding-bottom: 8px;
  margin-bottom: 12px;
  border-bottom: 1px solid var(--bg-200, #1c1c20);
}
.tier-label {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: #FF6B6B;
}
.tier-sublabel {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.45);
}

/* ── Card frame ───────────────────────────────────────────── */
.card-frame {
  border: 1px solid var(--bg-200, #1c1c20);
  background: rgba(255, 42, 42, 0.025);
  padding: 14px 16px 12px;
  position: relative;
}
.card-frame::before {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 2px; height: 100%;
  background: linear-gradient(to bottom, #FF2A2A, rgba(255, 42, 42, 0.15));
}

.card-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;
}
.card-label {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.10em;
  color: #FF6B6B;
}

.card-footer {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-top: 8px;
  margin-top: 10px;
  border-top: 1px dashed rgba(255, 255, 255, 0.08);
  font-size: 11px;
}

/* ── Leg row ──────────────────────────────────────────────── */
.leg-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
  cursor: pointer;
  transition: opacity 120ms ease;
}
.leg-row:hover { opacity: 0.85; }
.leg-row + .leg-row { border-top: 1px dashed rgba(255, 255, 255, 0.06); }

.leg-headshot {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 50%;
  border: 1px solid var(--bg-300, #26262c);
  flex-shrink: 0;
}

.leg-body {
  flex: 1;
  min-width: 0;
}
.leg-name {
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.85);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.leg-meta {
  display: flex;
  align-items: baseline;
  gap: 5px;
  flex-wrap: wrap;
  font-size: 10px;
  margin-top: 1px;
}
.leg-market {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  color: rgba(255, 255, 255, 0.45);
  text-transform: uppercase;
  margin-left: 4px;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid currentColor;
  font-weight: 600;
}
/* Market color heat-map (mirrors CardBlock.vue exactly).
   green = safest "will hit", through yellow/orange = medium,
   red = HR longshot. */
.market-hits {
  color: #4ade80;
  background: rgba(74, 222, 128, 0.08);
}
.market-rbi {
  color: #c084fc;
  background: rgba(192, 132, 252, 0.08);
}
.market-hrr-low {
  color: #fbbf24;
  background: rgba(251, 191, 36, 0.08);
}
.market-hrr-mid {
  color: #fb923c;
  background: rgba(251, 146, 60, 0.08);
}
.market-hrr-high {
  color: #f87171;
  background: rgba(248, 113, 113, 0.08);
}
.market-hr {
  color: #ef4444;
  background: rgba(239, 68, 68, 0.10);
}
.market-default {
  color: rgba(255, 255, 255, 0.55);
  background: rgba(255, 255, 255, 0.05);
}
.leg-numbers {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-top: 3px;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11px;
}
.leg-proj { color: rgba(255, 255, 255, 0.55); }
.leg-odds {
  color: rgba(255, 107, 107, 0.95);
  font-weight: 500;
}
.leg-edge { font-weight: 500; }

/* ── Leg status indicator (right side of leg row) ─────────── */
.leg-status {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.08em;
  padding: 3px 7px;
  border-radius: 2px;
  border: 1px solid currentColor;
  flex-shrink: 0;
}
.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.leg-status-pregame {
  color: rgba(255, 255, 255, 0.35);
  background: rgba(255, 255, 255, 0.02);
}
.leg-status-live {
  color: rgba(255, 200, 80, 0.95);
  background: rgba(255, 200, 80, 0.10);
  animation: leg-status-pulse 2.2s ease-in-out infinite;
}
.leg-status-hit {
  color: rgba(80, 220, 130, 1);
  background: rgba(80, 220, 130, 0.10);
}
.leg-status-busted {
  color: rgba(255, 95, 95, 1);
  background: rgba(255, 95, 95, 0.10);
}
.leg-status-void,
.leg-status-awaiting {
  color: rgba(255, 255, 255, 0.40);
  background: rgba(255, 255, 255, 0.03);
}

@keyframes leg-status-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.55; }
}

/* ── Card-level badge ─────────────────────────────────────── */
.badge {
  display: inline-block;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.10em;
  padding: 2px 7px;
  border-radius: 2px;
  line-height: 1;
  border: 1px solid currentColor;
}
.badge-pending {
  color: rgba(255, 200, 80, 0.85);
  background: rgba(255, 200, 80, 0.08);
  border-color: rgba(255, 200, 80, 0.45);
}
.badge-live {
  color: rgba(80, 220, 130, 1);
  background: rgba(80, 220, 130, 0.10);
  animation: leg-status-pulse 2.2s ease-in-out infinite;
}
.badge-win {
  color: rgba(80, 220, 130, 1);
  background: rgba(80, 220, 130, 0.10);
}
.badge-loss {
  color: rgba(255, 95, 95, 1);
  background: rgba(255, 95, 95, 0.10);
}
.badge-void {
  color: rgba(255, 255, 255, 0.45);
  background: rgba(255, 255, 255, 0.02);
}
</style>
