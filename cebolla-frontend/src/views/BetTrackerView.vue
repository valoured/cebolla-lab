<script setup>
// BetTrackerView.vue — the KrashBoard-killer page
// Shows: pending bets, settled bets, ROI by edge bucket, ROI by model version

import { onMounted, ref, computed } from 'vue'
import { useBetLog } from '../composables/useBetLog'

const {
  bets,
  loading,
  initialized,
  fetchBets,
  updateBet,
  deleteBet,
  fetchRoiByEdge,
  fetchRoiByModel,
  totalStaked,
  totalPnl,
  pendingCount,
  settledCount,
  overallRoi,
  winRate,
} = useBetLog()

const roiByEdge = ref([])
const roiByModel = ref([])
const filter = ref('all')   // 'all' | 'pending' | 'settled'

async function refresh() {
  await fetchBets()
  roiByEdge.value  = await fetchRoiByEdge()
  roiByModel.value = await fetchRoiByModel()
}

onMounted(refresh)

const filteredBets = computed(() => {
  const all = bets.value
  if (filter.value === 'pending') return all.filter(b => b.result === 'pending')
  if (filter.value === 'settled') return all.filter(b => b.result !== 'pending')
  return all
})

function fmtOdds(n) {
  if (n == null) return '—'
  return (n >= 0 ? '+' : '') + n
}
function fmtMoney(n) {
  if (n == null) return '—'
  return (n < 0 ? '-' : '') + '$' + Math.abs(n).toFixed(2)
}
function fmtPct(n) {
  if (n == null) return '—'
  return (n >= 0 ? '+' : '') + (n * 100).toFixed(2) + '%'
}
function fmtEdge(e) {
  if (e == null) return '—'
  const pct = e * 100
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}
function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
         ' ' +
         d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

function resultColor(r) {
  if (r === 'win')  return 'text-emerald-400'
  if (r === 'loss') return 'text-accent-red'
  if (r === 'push') return 'text-fg-300'
  if (r === 'void') return 'text-fg-400'
  return 'text-yellow-400/80'   // pending
}

async function markResult(bet, result) {
  // Manual override (escape hatch if auto-settle missed it)
  if (!confirm(`Mark this bet as ${result.toUpperCase()}?`)) return
  // Recompute pnl from odds + stake
  const odds = parseInt(bet.american_odds, 10)
  const stake = parseFloat(bet.stake)
  let pnl = 0
  let payout = 0
  if (result === 'win') {
    const profit = odds >= 0 ? (stake * odds / 100) : (stake * 100 / -odds)
    pnl = profit
    payout = stake + profit
  } else if (result === 'loss') {
    pnl = -stake
    payout = 0
  } else if (result === 'push' || result === 'void') {
    pnl = 0
    payout = stake
  }
  await updateBet(bet.id, {
    result,
    pnl,
    payout,
    settled_at: new Date().toISOString(),
  })
  await refresh()
}

async function onDelete(bet) {
  if (!confirm(`Delete bet on ${bet.player_name}? This cannot be undone.`)) return
  await deleteBet(bet.id)
  await refresh()
}
</script>

<template>
  <div class="bet-tracker-view py-6 px-4 max-w-[1280px] mx-auto">
    <!-- Header -->
    <div class="mb-6">
      <div class="label-bracket text-accent-red mb-1">[ M.05 / BET TRACKER ]</div>
      <h1 class="font-display text-2xl text-fg-50">Activity Log</h1>
      <p class="text-fg-400 text-sm mt-1">
        Cebolla's feedback loop. Log every bet with edge at placement → see which edge buckets actually print.
      </p>
    </div>

    <!-- Top stat cards -->
    <div class="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-6">
      <div class="stat-card">
        <div class="label-bracket !text-[9px] opacity-60 mb-1">[ TOTAL STAKED ]</div>
        <div class="display-num text-lg text-fg-50">{{ fmtMoney(totalStaked) }}</div>
      </div>
      <div class="stat-card">
        <div class="label-bracket !text-[9px] opacity-60 mb-1">[ TOTAL P/L ]</div>
        <div class="display-num text-lg"
             :class="totalPnl > 0 ? 'text-emerald-400' : totalPnl < 0 ? 'text-accent-red' : 'text-fg-50'">
          {{ fmtMoney(totalPnl) }}
        </div>
      </div>
      <div class="stat-card">
        <div class="label-bracket !text-[9px] opacity-60 mb-1">[ ROI ]</div>
        <div class="display-num text-lg"
             :class="overallRoi != null && overallRoi > 0 ? 'text-emerald-400' : overallRoi != null && overallRoi < 0 ? 'text-accent-red' : 'text-fg-300'">
          {{ overallRoi == null ? '—' : (overallRoi * 100).toFixed(1) + '%' }}
        </div>
      </div>
      <div class="stat-card">
        <div class="label-bracket !text-[9px] opacity-60 mb-1">[ WIN RATE ]</div>
        <div class="display-num text-lg text-fg-50">
          {{ winRate == null ? '—' : (winRate * 100).toFixed(0) + '%' }}
        </div>
      </div>
      <div class="stat-card">
        <div class="label-bracket !text-[9px] opacity-60 mb-1">[ BETS ]</div>
        <div class="display-num text-lg text-fg-50">
          {{ settledCount }}<span class="text-fg-400 text-xs ml-1">+ {{ pendingCount }} open</span>
        </div>
      </div>
    </div>

    <!-- ROI by edge bucket -->
    <div class="mb-6">
      <div class="label-bracket text-accent-red mb-2">[ ROI × EDGE BUCKET ]</div>
      <div v-if="!roiByEdge.length" class="text-fg-400 text-xs italic">
        Sin datos &mdash; settle some bets to see your model's edge calibration.
      </div>
      <div v-else class="overflow-x-auto border border-bg-200/40 rounded-sm">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-bg-200/60 text-fg-300 label-bracket !text-[9px]">
              <th class="text-left py-2 px-3">BUCKET</th>
              <th class="text-right py-2 px-3">BETS</th>
              <th class="text-right py-2 px-3">STAKED</th>
              <th class="text-right py-2 px-3">P/L</th>
              <th class="text-right py-2 px-3">ROI</th>
              <th class="text-right py-2 px-3">WIN %</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in roiByEdge" :key="r.bucket" class="border-b border-bg-200/30">
              <td class="py-2 px-3 text-fg-200">{{ r.bucket }}</td>
              <td class="py-2 px-3 text-right display-num text-fg-300">{{ r.bets }}</td>
              <td class="py-2 px-3 text-right display-num text-fg-300">{{ fmtMoney(r.total_staked) }}</td>
              <td class="py-2 px-3 text-right display-num"
                  :class="r.total_pnl > 0 ? 'text-emerald-400' : r.total_pnl < 0 ? 'text-accent-red' : 'text-fg-300'">
                {{ fmtMoney(r.total_pnl) }}
              </td>
              <td class="py-2 px-3 text-right display-num"
                  :class="r.roi > 0 ? 'text-emerald-400' : r.roi < 0 ? 'text-accent-red' : 'text-fg-300'">
                {{ r.roi == null ? '—' : (r.roi * 100).toFixed(1) + '%' }}
              </td>
              <td class="py-2 px-3 text-right display-num text-fg-300">
                {{ r.win_rate == null ? '—' : (r.win_rate * 100).toFixed(0) + '%' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ROI by model version (only if >1 model) -->
    <div v-if="roiByModel.length > 1" class="mb-6">
      <div class="label-bracket text-accent-red mb-2">[ ROI × MODEL VERSION ]</div>
      <div class="overflow-x-auto border border-bg-200/40 rounded-sm">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-bg-200/60 text-fg-300 label-bracket !text-[9px]">
              <th class="text-left py-2 px-3">MODEL</th>
              <th class="text-right py-2 px-3">BETS</th>
              <th class="text-right py-2 px-3">STAKED</th>
              <th class="text-right py-2 px-3">P/L</th>
              <th class="text-right py-2 px-3">ROI</th>
              <th class="text-right py-2 px-3">WIN %</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in roiByModel" :key="r.model_version" class="border-b border-bg-200/30">
              <td class="py-2 px-3 display-num text-fg-200">{{ r.model_version }}</td>
              <td class="py-2 px-3 text-right display-num text-fg-300">{{ r.bets }}</td>
              <td class="py-2 px-3 text-right display-num text-fg-300">{{ fmtMoney(r.total_staked) }}</td>
              <td class="py-2 px-3 text-right display-num"
                  :class="r.total_pnl > 0 ? 'text-emerald-400' : r.total_pnl < 0 ? 'text-accent-red' : 'text-fg-300'">
                {{ fmtMoney(r.total_pnl) }}
              </td>
              <td class="py-2 px-3 text-right display-num"
                  :class="r.roi > 0 ? 'text-emerald-400' : r.roi < 0 ? 'text-accent-red' : 'text-fg-300'">
                {{ r.roi == null ? '—' : (r.roi * 100).toFixed(1) + '%' }}
              </td>
              <td class="py-2 px-3 text-right display-num text-fg-300">
                {{ r.win_rate == null ? '—' : (r.win_rate * 100).toFixed(0) + '%' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Bet log table -->
    <div class="mb-2 flex items-center justify-between">
      <div class="label-bracket text-accent-red">[ BET LOG ]</div>
      <div class="flex gap-1">
        <button @click="filter='all'"     :class="['filter-pill', filter==='all'     && 'active']">All</button>
        <button @click="filter='pending'" :class="['filter-pill', filter==='pending' && 'active']">Pending ({{ pendingCount }})</button>
        <button @click="filter='settled'" :class="['filter-pill', filter==='settled' && 'active']">Settled ({{ settledCount }})</button>
      </div>
    </div>

    <div v-if="loading && !initialized" class="text-fg-400 text-sm italic py-8 text-center">
      Cargando…
    </div>
    <div v-else-if="!filteredBets.length" class="text-fg-400 text-sm italic py-8 text-center border border-bg-200/30 rounded-sm">
      <template v-if="filter === 'all'">
        Sin apuestas registradas. Open the HR Report and tap "[ LOG ]" on a row to start tracking.
      </template>
      <template v-else>Sin apuestas en este filtro.</template>
    </div>
    <div v-else class="overflow-x-auto border border-bg-200/40 rounded-sm">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-bg-200/60 text-fg-300 label-bracket !text-[9px]">
            <th class="text-left py-2 px-3">PLACED</th>
            <th class="text-left py-2 px-3">PLAYER</th>
            <th class="text-left py-2 px-3">MARKET</th>
            <th class="text-right py-2 px-3">ODDS</th>
            <th class="text-right py-2 px-3">STAKE</th>
            <th class="text-right py-2 px-3">EDGE</th>
            <th class="text-left py-2 px-3">MODEL</th>
            <th class="text-center py-2 px-3">RESULT</th>
            <th class="text-right py-2 px-3">P/L</th>
            <th class="text-right py-2 px-3"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in filteredBets" :key="b.id" class="border-b border-bg-200/30 hover:bg-bg-100/20">
            <td class="py-2 px-3 text-fg-400 text-xs">{{ fmtDate(b.placed_at) }}</td>
            <td class="py-2 px-3 text-fg-100">
              {{ b.player_name }}
              <span v-if="b.away_abbrev && b.home_abbrev" class="text-fg-500 text-[10px] ml-1">
                {{ b.away_abbrev }}@{{ b.home_abbrev }}
              </span>
            </td>
            <td class="py-2 px-3 text-fg-300 text-xs">
              {{ b.market }} · {{ b.side }}<span v-if="b.line"> {{ b.line }}</span>
            </td>
            <td class="py-2 px-3 text-right display-num text-fg-200">{{ fmtOdds(b.american_odds) }}</td>
            <td class="py-2 px-3 text-right display-num text-fg-200">{{ fmtMoney(b.stake) }}</td>
            <td class="py-2 px-3 text-right display-num"
                :class="b.edge_at_placement == null ? 'text-fg-500'
                       : b.edge_at_placement >= 0 ? 'text-emerald-400' : 'text-accent-red'">
              {{ fmtEdge(b.edge_at_placement) }}
            </td>
            <td class="py-2 px-3 text-fg-400 text-xs display-num">{{ b.model_version || '—' }}</td>
            <td class="py-2 px-3 text-center">
              <span :class="['label-bracket text-[10px]', resultColor(b.result)]">
                {{ (b.result || 'pending').toUpperCase() }}
              </span>
            </td>
            <td class="py-2 px-3 text-right display-num"
                :class="b.pnl > 0 ? 'text-emerald-400' : b.pnl < 0 ? 'text-accent-red' : 'text-fg-400'">
              {{ b.pnl == null ? '—' : fmtMoney(b.pnl) }}
            </td>
            <td class="py-2 px-3 text-right">
              <div class="flex justify-end gap-1">
                <button
                  v-if="b.result === 'pending'"
                  @click="markResult(b, 'win')"
                  class="action-pill text-emerald-400"
                  title="Mark win"
                >W</button>
                <button
                  v-if="b.result === 'pending'"
                  @click="markResult(b, 'loss')"
                  class="action-pill text-accent-red"
                  title="Mark loss"
                >L</button>
                <button
                  v-if="b.result === 'pending'"
                  @click="markResult(b, 'void')"
                  class="action-pill text-fg-400"
                  title="Void"
                >V</button>
                <button
                  @click="onDelete(b)"
                  class="action-pill text-fg-500 hover:text-accent-red"
                  title="Delete"
                >×</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.stat-card {
  background: rgba(255,255,255,0.02);
  border: 1px solid rgba(255,255,255,0.08);
  padding: 8px 10px;
  border-radius: 2px;
}
.filter-pill {
  font-family: 'IBM Plex Sans', sans-serif;
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 6px 12px;
  min-height: 36px;
  display: inline-flex;
  align-items: center;
  border: 1px solid rgba(255,255,255,0.10);
  color: rgb(180,180,180);
  background: transparent;
  border-radius: 2px;
  transition: all 0.15s;
}
.filter-pill:hover {
  color: rgb(245,245,245);
  border-color: rgba(255,255,255,0.25);
}
.filter-pill.active {
  color: var(--color-accent-red, #FF2A2A);
  border-color: var(--color-accent-red, #FF2A2A);
}
/* Action pills (W / L / V / × on each bet row) — bumped from 22px to
   36x36 because these are critical destructive/state-changing actions
   and mis-tapping × Delete vs L Loss is a real consequence. */
.action-pill {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  width: 36px;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(255,255,255,0.10);
  background: transparent;
  border-radius: 2px;
  transition: all 0.12s;
}
.action-pill:hover {
  border-color: currentColor;
}
</style>
