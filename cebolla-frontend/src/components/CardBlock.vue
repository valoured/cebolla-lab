<script setup>
/**
 * CardBlock — One Cebolla Card row with its stacked legs.
 *
 * Consumed by CardsView. Card already has `legs` injected by parent
 * (via legsByCard lookup) and helpers like `legStatusIndicator` etc.
 *
 * Renders:
 *   - Header: tier label + card label + status badge
 *   - Each leg: headshot, name, team vs opp, market, odds, status indicator
 *   - Footer: stake/payout line
 */

import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'

const props = defineProps({
  card:       { type: Object, required: true },
  legs:       { type: Array,  default: () => [] },
  gamesById:  { type: Object, default: () => ({}) },
})

const router = useRouter()

function openPlayer(mlbamId) {
  if (!mlbamId) return
  router.push({ name: 'player', params: { playerId: mlbamId } })
}

// ── Status helpers (same logic as CardsView, replicated for encapsulation) ──
function isGameLive(gameId) {
  const g = props.gamesById[gameId]
  if (!g) return false
  const st = (g.status || '').toLowerCase()
  if (st === 'live' || st === 'in_progress' || st === 'in progress') return true
  if (st === 'scheduled' || st === 'preview') {
    if (g.game_time_utc) {
      const start = new Date(g.game_time_utc).getTime()
      const now = Date.now()
      if (now > start + 5 * 60_000 && now < start + 4 * 60 * 60_000) return true
    }
  }
  return false
}
function isGameFinal(gameId) {
  const g = props.gamesById[gameId]
  if (!g) return false
  const st = (g.status || '').toLowerCase()
  return st === 'final' || st === 'completed'
}

function legStatusIndicator(leg) {
  if (leg.status === 'win')  return { kind: 'hit',     label: 'HIT'   }
  if (leg.status === 'loss') return { kind: 'busted',  label: 'MISS'  }
  if (leg.status === 'void') return { kind: 'void',    label: 'VOID'  }
  if (isGameLive(leg.game_id))  return { kind: 'live',     label: 'LIVE'  }
  if (isGameFinal(leg.game_id)) return { kind: 'awaiting', label: 'GRADING' }
  return { kind: 'pregame', label: 'PREGAME' }
}

const cardStatusBadge = computed(() => {
  const c = props.card
  if (c.status === 'win')  return { kind: 'win',  label: 'CASHED' }
  if (c.status === 'loss') return { kind: 'loss', label: 'BUSTED' }
  if (c.status === 'void') return { kind: 'void', label: 'VOID'   }
  if (props.legs.some(l => isGameLive(l.game_id))) {
    return { kind: 'live', label: 'LIVE' }
  }
  return { kind: 'pending', label: 'PENDING' }
})

// ── Formatters ────────────────────────────────────────────
function fmtOdds(n) {
  if (n == null) return '—'
  return n > 0 ? `+${n}` : `${n}`
}
function fmtPct(n, d = 1) {
  if (n == null || !Number.isFinite(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(d)}%`
}
function fmtMoney(n, signed = false) {
  if (n == null || !Number.isFinite(Number(n))) return '—'
  const v = Number(n)
  const sign = signed ? (v > 0 ? '+' : (v < 0 ? '-' : '')) : (v < 0 ? '-' : '')
  return `${sign}$${Math.abs(v).toFixed(2)}`
}
function marketLabel(market, line) {
  if (market === 'hr_anytime') return 'HR'
  if (market === 'hits_yes')   return `Hits O${line ?? 0.5}`
  if (market === 'rbi_yes')    return `RBI O${line ?? 0.5}`
  if (market && market.startsWith('h_r_rbi_')) {
    return `H+R+RBI O${market.replace('h_r_rbi_', '')}`
  }
  return market || '?'
}
</script>

<template>
  <div class="card-frame">
    <!-- Header -->
    <div class="card-header">
      <div class="flex items-baseline gap-2 flex-wrap">
        <span class="card-label">{{ card.label || 'Card' }}</span>
        <span v-if="card.ev_per_dollar" class="card-ev"
              :class="card.ev_per_dollar >= 0.1 ? 'text-signal-400' : 'text-fg-500'">
          EV +{{ (card.ev_per_dollar * 100).toFixed(0) }}%
        </span>
      </div>
      <span class="badge" :class="`badge-${cardStatusBadge.kind}`">
        {{ cardStatusBadge.label }}
      </span>
    </div>

    <!-- Legs -->
    <div>
      <div v-for="leg in legs" :key="leg.id" class="leg-row" @click="openPlayer(leg.player_mlbam_id)">
        <img v-if="leg.player_mlbam_id"
             :src="playerHeadshotUrl(leg.player_mlbam_id)"
             :alt="leg.player_name"
             class="leg-headshot"
             @error="hideOnError" />
        <div class="leg-body">
          <div class="leg-name">{{ leg.player_name }}</div>
          <div class="leg-meta">
            <span class="label-bracket text-signal-400">{{ leg.team_abbrev }}</span>
            <span class="text-fg-500 italic">vs</span>
            <span class="label-bracket text-fg-600">{{ leg.opponent_abbrev }}</span>
            <span class="leg-market">{{ marketLabel(leg.market, leg.line) }}</span>
          </div>
          <div class="leg-numbers">
            <span class="leg-proj">{{ fmtPct(leg.projected_prob) }} proj</span>
            <span class="leg-odds">{{ fmtOdds(leg.american_odds) }}</span>
            <span class="leg-edge"
                  :class="Number(leg.edge) > 0 ? 'text-signal-400' : 'text-fg-500'">
              {{ Number(leg.edge) >= 0 ? '+' : '' }}{{ fmtPct(leg.edge) }} edge
            </span>
          </div>
        </div>
        <span class="leg-status" :class="`leg-status-${legStatusIndicator(leg).kind}`">
          <span class="status-dot"></span>
          {{ legStatusIndicator(leg).label }}
        </span>
      </div>
    </div>

    <!-- Footer: stake → payout line -->
    <div class="card-footer">
      <span class="text-fg-500">${{ Number(card.stake_rec).toFixed(0) }} stake</span>
      <span class="text-fg-500">·</span>
      <span class="text-fg-500">{{ fmtOdds(card.combined_odds) }}</span>
      <span class="text-fg-500">·</span>
      <span v-if="card.status === 'win'" class="text-signal-400 display-num">
        cashed {{ fmtMoney(card.payout, true) }}
      </span>
      <span v-else-if="card.status === 'loss'" class="text-edge-cold-1 display-num">
        busted {{ fmtMoney(card.payout, true) }}
      </span>
      <span v-else class="text-fg-700 display-num">
        → ${{ Number(card.payout_if_hit).toFixed(2) }} if hits
      </span>
    </div>
  </div>
</template>

<style scoped>
/* All same styles as CardsView — duplicated for component isolation */
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
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.10em;
  color: #FF6B6B;
}
.card-ev {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  font-weight: 600;
}

.card-footer {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-top: 8px;
  margin-top: 10px;
  border-top: 1px dashed rgba(255, 255, 255, 0.08);
  font-size: 11px;
  font-family: 'JetBrains Mono', ui-monospace, monospace;
}

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

.leg-body { flex: 1; min-width: 0; }
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
.leg-odds { color: rgba(255, 107, 107, 0.95); font-weight: 500; }
.leg-edge { font-weight: 500; }

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
