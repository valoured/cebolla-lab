<script setup>
/**
 * CardsView.vue — Cebolla Cards
 *
 * The branded public picks page. Two sections:
 *
 *   1. Today's Card — the day's locked PODs (HR + HRR) at the top, plus
 *      a "secondary picks" rail showing the next ~5 highest-edge plays
 *      the model likes today across all markets. The POD is the headline
 *      bet; secondaries are "model also liked these."
 *
 *   2. Historical Ledger — every settled POD ever, newest first. Date,
 *      player, market, odds, result, P&L. Real receipts.
 *
 * No bet-placing happens here — these are research recommendations.
 * The Bet Log (/bets) is separate (user's personal bet tracking).
 */

import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { supabase } from '../supabase.js'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'
import LoadingBrand from '../components/LoadingBrand.vue'

const router = useRouter()

const pods = ref([])
const todaySecondaries = ref([])    // top secondary picks for today
const loading = ref(true)
const error = ref(null)

// ── Today's ET date ───────────────────────────────────────────
function todayIsoFn() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date())
}
const todayIso = todayIsoFn()

// ── Load ──────────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    // All historical PODs
    const { data: podData, error: pe } = await supabase
      .from('pods')
      .select('id, pod_date, market_class, market, projected_prob, edge, ' +
              'american_odds, status, payout, stake, player_name, ' +
              'team_abbrev, opponent_abbrev, player_mlbam_id, combined_score')
      .order('pod_date', { ascending: false })
      .limit(500)
    if (pe) throw pe
    pods.value = podData || []

    // Today's secondary picks — pull top-edge projections for today's
    // games that aren't already a POD. Surfaces the "model's also liked"
    // plays at a glance.
    await loadSecondaries()
  } catch (e) {
    console.error('[CardsView] load failed:', e)
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

async function loadSecondaries() {
  // 1) Get today's game IDs
  const { data: games } = await supabase
    .from('games')
    .select('id, away_team_id, home_team_id, ' +
            'away_team:teams!games_away_team_id_fkey(abbrev), ' +
            'home_team:teams!games_home_team_id_fkey(abbrev)')
    .eq('game_date', todayIso)
  if (!games || !games.length) {
    todaySecondaries.value = []
    return
  }
  const gameIds = games.map(g => g.id)
  const gameById = Object.fromEntries(games.map(g => [g.id, g]))

  // 2) Pull HR + HRR (1.5 line) projections with positive edge
  const { data: projs } = await supabase
    .from('projections')
    .select('id, game_id, player_id, market, projected_prob, edge, best_american_odds, best_book')
    .in('game_id', gameIds)
    .in('market', ['hr_anytime', 'h_r_rbi_1.5'])
    .gte('edge', 0.03)            // floor: 3% edge minimum
    .not('best_american_odds', 'is', null)
    .order('edge', { ascending: false })
    .limit(40)
  if (!projs || !projs.length) {
    todaySecondaries.value = []
    return
  }

  // 3) Exclude rows that are already today's POD
  const podKeys = new Set(
    pods.value
      .filter(p => p.pod_date === todayIso)
      .map(p => `${p.player_mlbam_id}_${p.market}`)
  )

  // 4) Need player names + mlbam_ids — single batch lookup
  const playerIds = [...new Set(projs.map(p => p.player_id))]
  const { data: players } = await supabase
    .from('players')
    .select('id, name, mlbam_id, team_id')
    .in('id', playerIds)
  const playerById = Object.fromEntries((players || []).map(p => [p.id, p]))

  // 5) Build secondary cards, drop POD-duplicates, take top 5 by edge
  const secondaries = []
  const seenPlayers = new Set()
  for (const proj of projs) {
    const player = playerById[proj.player_id]
    if (!player) continue
    const key = `${player.mlbam_id}_${proj.market}`
    if (podKeys.has(key)) continue
    // Avoid showing the same player twice (could appear in both HR and HRR)
    if (seenPlayers.has(player.id)) continue
    seenPlayers.add(player.id)

    const game = gameById[proj.game_id]
    if (!game) continue
    const isHome = player.team_id === game.home_team_id
    const own = (isHome ? game.home_team : game.away_team)?.abbrev
    const opp = (isHome ? game.away_team : game.home_team)?.abbrev

    secondaries.push({
      player_id: player.id,
      player_mlbam_id: player.mlbam_id,
      player_name: player.name,
      team_abbrev: own,
      opponent_abbrev: opp,
      market: proj.market,
      projected_prob: Number(proj.projected_prob),
      edge: Number(proj.edge),
      american_odds: proj.best_american_odds,
      book: proj.best_book,
    })
    if (secondaries.length >= 5) break
  }
  todaySecondaries.value = secondaries
}
onMounted(load)

// ── Derived ───────────────────────────────────────────────────
const todaysPods = computed(() =>
  pods.value.filter(p => p.pod_date === todayIso)
)
const todaysHrPod  = computed(() => todaysPods.value.find(p => (p.market_class || 'hr') === 'hr') || null)
const todaysHrrPod = computed(() => todaysPods.value.find(p => (p.market_class || 'hr') === 'hrr') || null)

const historicalPods = computed(() =>
  pods.value.filter(p => p.pod_date !== todayIso && ['win', 'loss', 'push', 'void'].includes(p.status))
)

// ── Formatters ────────────────────────────────────────────────
function fmtOdds(n) {
  if (n == null) return '—'
  return n > 0 ? `+${n}` : `${n}`
}
function fmtPct(n) {
  if (n == null) return '—'
  return `${(Number(n) * 100).toFixed(1)}%`
}
function fmtEdge(n) {
  if (n == null) return '—'
  const pct = Number(n) * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(1)}%`
}
function fmtMoney(n) {
  if (n == null || !Number.isFinite(Number(n))) return '—'
  const v = Number(n)
  const sign = v > 0 ? '+' : (v < 0 ? '-' : '')
  return `${sign}$${Math.abs(v).toFixed(2)}`
}
function fmtDate(s) {
  if (!s) return ''
  const [y, m, d] = s.split('-').map(Number)
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  return `${months[m-1]} ${d}`
}
function marketLabel(m) {
  if (m === 'hr_anytime') return 'HR Anytime'
  if (m === 'hr_0.5') return 'HR Anytime'
  if (m && m.startsWith('h_r_rbi_')) {
    const line = m.replace('h_r_rbi_', '')
    return `H+R+RBI O${line}`
  }
  return m || '?'
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
  if (!status) return '?'
  return status.toUpperCase()
}

function openPlayer(mlbamId) {
  if (!mlbamId) return
  router.push({ name: 'player', params: { playerId: mlbamId } })
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
        Today's locked picks at the top, plus the model's secondary plays it
        also liked. Below, every Cebolla Card ever — the full ledger.
      </p>
    </section>

    <LoadingBrand v-if="loading" />

    <div v-else-if="error" class="px-6 py-12 text-edge-cold-1">
      Error: {{ error }}
    </div>

    <template v-else>
      <!-- ── TODAY'S CARD ────────────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-8">
        <div class="flex items-baseline gap-3 mb-3">
          <h2 class="display-text text-xl text-fg-800">Today's Card</h2>
          <span class="label-bracket !text-[8px] text-fg-500">{{ fmtDate(todayIso) }}</span>
        </div>

        <!-- POD slots -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3 mb-4">
          <!-- HR POD -->
          <div class="card-slot">
            <div class="flex items-baseline justify-between mb-2">
              <span class="label-caps text-signal-400">★ HR POD</span>
              <span v-if="todaysHrPod" class="badge !text-[9px]"
                    :class="statusBadgeClass(todaysHrPod.status)">
                {{ statusLabel(todaysHrPod.status) }}
              </span>
            </div>
            <div v-if="todaysHrPod" class="flex items-center gap-3 cursor-pointer hover:opacity-90 transition"
                 @click="openPlayer(todaysHrPod.player_mlbam_id)">
              <img v-if="todaysHrPod.player_mlbam_id"
                   :src="playerHeadshotUrl(todaysHrPod.player_mlbam_id)"
                   :alt="todaysHrPod.player_name"
                   class="card-headshot"
                   @error="hideOnError" />
              <div class="flex-1 min-w-0">
                <div class="display-text text-lg text-fg-800 truncate">{{ todaysHrPod.player_name }}</div>
                <div class="flex items-baseline gap-1.5 flex-wrap text-[10px] mt-0.5">
                  <span class="label-bracket text-signal-400">{{ todaysHrPod.team_abbrev }}</span>
                  <span class="text-fg-500 italic">vs</span>
                  <span class="label-bracket text-fg-600">{{ todaysHrPod.opponent_abbrev }}</span>
                  <span class="label-caps !text-[8px] ml-1">{{ marketLabel(todaysHrPod.market) }}</span>
                </div>
                <div class="flex items-baseline gap-3 mt-1.5 text-[11px]">
                  <span class="text-fg-500">{{ fmtPct(todaysHrPod.projected_prob) }} proj</span>
                  <span class="display-num text-signal-200">{{ fmtOdds(todaysHrPod.american_odds) }}</span>
                  <span class="display-num"
                        :class="todaysHrPod.edge > 0 ? 'text-signal-400' : 'text-fg-500'">
                    {{ fmtEdge(todaysHrPod.edge) }} edge
                  </span>
                </div>
              </div>
            </div>
            <div v-else class="text-fg-500 text-xs italic py-4 text-center">
              No HR pick locked yet today.
            </div>
          </div>

          <!-- HRR POD -->
          <div class="card-slot">
            <div class="flex items-baseline justify-between mb-2">
              <span class="label-caps text-signal-400">★ H+R+RBI POD</span>
              <span v-if="todaysHrrPod" class="badge !text-[9px]"
                    :class="statusBadgeClass(todaysHrrPod.status)">
                {{ statusLabel(todaysHrrPod.status) }}
              </span>
            </div>
            <div v-if="todaysHrrPod" class="flex items-center gap-3 cursor-pointer hover:opacity-90 transition"
                 @click="openPlayer(todaysHrrPod.player_mlbam_id)">
              <img v-if="todaysHrrPod.player_mlbam_id"
                   :src="playerHeadshotUrl(todaysHrrPod.player_mlbam_id)"
                   :alt="todaysHrrPod.player_name"
                   class="card-headshot"
                   @error="hideOnError" />
              <div class="flex-1 min-w-0">
                <div class="display-text text-lg text-fg-800 truncate">{{ todaysHrrPod.player_name }}</div>
                <div class="flex items-baseline gap-1.5 flex-wrap text-[10px] mt-0.5">
                  <span class="label-bracket text-signal-400">{{ todaysHrrPod.team_abbrev }}</span>
                  <span class="text-fg-500 italic">vs</span>
                  <span class="label-bracket text-fg-600">{{ todaysHrrPod.opponent_abbrev }}</span>
                  <span class="label-caps !text-[8px] ml-1">{{ marketLabel(todaysHrrPod.market) }}</span>
                </div>
                <div class="flex items-baseline gap-3 mt-1.5 text-[11px]">
                  <span class="text-fg-500">{{ fmtPct(todaysHrrPod.projected_prob) }} proj</span>
                  <span class="display-num text-signal-200">{{ fmtOdds(todaysHrrPod.american_odds) }}</span>
                  <span class="display-num"
                        :class="todaysHrrPod.edge > 0 ? 'text-signal-400' : 'text-fg-500'">
                    {{ fmtEdge(todaysHrrPod.edge) }} edge
                  </span>
                </div>
              </div>
            </div>
            <div v-else class="text-fg-500 text-xs italic py-4 text-center">
              No HRR pick locked yet today.
            </div>
          </div>
        </div>

        <!-- Secondary picks rail -->
        <div v-if="todaySecondaries.length">
          <div class="flex items-baseline gap-2 mb-2">
            <span class="label-caps text-fg-500">also liked</span>
            <span class="label-bracket !text-[8px] text-fg-500">model picks &gt; 3% edge</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left border-b border-bg-200">
                  <th class="label-caps !text-[8px] py-2 px-3">Player</th>
                  <th class="label-caps !text-[8px] py-2 px-2 text-right">Market</th>
                  <th class="label-caps !text-[8px] py-2 px-2 text-right">Odds</th>
                  <th class="label-caps !text-[8px] py-2 px-2 text-right">Proj</th>
                  <th class="label-caps !text-[8px] py-2 px-2 text-right">Edge</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in todaySecondaries" :key="`${s.player_id}_${s.market}`"
                    class="border-b border-bg-200/40 hover:bg-bg-100/40 transition cursor-pointer"
                    @click="openPlayer(s.player_mlbam_id)">
                  <td class="py-2 px-3">
                    <span class="text-fg-700">{{ s.player_name }}</span>
                    <span class="font-mono text-[9px] text-fg-500 ml-2">{{ s.team_abbrev }} vs {{ s.opponent_abbrev }}</span>
                  </td>
                  <td class="py-2 px-2 text-right label-caps !text-[9px]">{{ marketLabel(s.market) }}</td>
                  <td class="py-2 px-2 text-right display-num text-signal-200">{{ fmtOdds(s.american_odds) }}</td>
                  <td class="py-2 px-2 text-right display-num text-fg-700">{{ fmtPct(s.projected_prob) }}</td>
                  <td class="py-2 px-2 text-right display-num text-signal-400">{{ fmtEdge(s.edge) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- ── HISTORICAL LEDGER ───────────────────────────────── -->
      <section class="px-4 sm:px-6 mb-10">
        <div class="flex items-baseline gap-3 mb-3">
          <h2 class="display-text text-xl text-fg-800">Card History</h2>
          <span class="label-bracket !text-[8px] text-fg-500">M.03.b</span>
          <span class="text-fg-500 text-xs">·  {{ historicalPods.length }} settled</span>
        </div>

        <div v-if="!historicalPods.length"
             class="bg-bg-50 border border-bg-200 px-4 py-8 text-center">
          <div class="display-text text-lg text-fg-500 italic mb-1">No settled cards yet</div>
          <p class="text-fg-500 text-xs">
            Once today's picks settle (post-game), the historical ledger will start populating here.
          </p>
        </div>

        <div v-else class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left border-b border-bg-200">
                <th class="label-caps !text-[8px] py-2 px-3">Date</th>
                <th class="label-caps !text-[8px] py-2 px-3">Player</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">Market</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">Odds</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">Proj</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-center">Result</th>
                <th class="label-caps !text-[8px] py-2 px-2 text-right">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="pod in historicalPods" :key="pod.id"
                  class="border-b border-bg-200/40 hover:bg-bg-100/40 transition cursor-pointer"
                  @click="openPlayer(pod.player_mlbam_id)">
                <td class="py-2 px-3 text-fg-500 font-mono text-[11px]">{{ fmtDate(pod.pod_date) }}</td>
                <td class="py-2 px-3">
                  <span class="text-fg-700">{{ pod.player_name }}</span>
                  <span class="font-mono text-[9px] text-fg-500 ml-2">{{ pod.team_abbrev }} vs {{ pod.opponent_abbrev }}</span>
                </td>
                <td class="py-2 px-2 text-right label-caps !text-[9px]">{{ marketLabel(pod.market) }}</td>
                <td class="py-2 px-2 text-right display-num">{{ fmtOdds(pod.american_odds) }}</td>
                <td class="py-2 px-2 text-right display-num text-fg-700">{{ fmtPct(pod.projected_prob) }}</td>
                <td class="py-2 px-2 text-center">
                  <span class="badge !text-[9px]" :class="statusBadgeClass(pod.status)">
                    {{ statusLabel(pod.status) }}
                  </span>
                </td>
                <td class="py-2 px-2 text-right display-num"
                    :class="pod.status === 'win' ? 'text-signal-400' : (pod.status === 'loss' ? 'text-edge-cold-1' : 'text-fg-500')">
                  <span v-if="pod.status === 'pending'">—</span>
                  <span v-else>{{ fmtMoney(pod.payout) }}</span>
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
.card-slot {
  border: 1px solid var(--bg-200, #1c1c20);
  background: rgba(255, 42, 42, 0.03);
  padding: 12px 14px;
  position: relative;
}
.card-slot::before {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 2px; height: 100%;
  background: linear-gradient(to bottom, #FF2A2A, rgba(255, 42, 42, 0.20));
}
.card-headshot {
  width: 56px;
  height: 56px;
  object-fit: cover;
  border-radius: 50%;
  border: 1px solid var(--bg-300, #26262c);
}

/* Badges — match PODView convention so badges feel consistent across pages */
.badge {
  display: inline-block;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.10em;
  padding: 2px 6px;
  border-radius: 2px;
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
