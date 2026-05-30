<script setup>
/**
 * CardBlock — One Cebolla Card with its stacked legs.
 *
 * Consumed by CardsView. Parent passes:
 *   - card:      the row from the `cards` table
 *   - legs:      array of card_legs rows for this card
 *   - gamesById: lookup map for game status (live/final classification)
 *
 * Renders:
 *   - Header: card label + EV pill + overall status badge
 *   - Each leg: headshot, name, team vs opp + market chip, proj/odds/edge,
 *               leg status indicator
 *   - Footer: stake · combined odds · (cashed/busted/voided/payout-if-hit)
 *
 * Status helpers (isGameLive / isGameFinal) route through the shared
 * src/utils/gameStatus.js module so CardBlock and CardsView stay in
 * lockstep on the MLB status-string keyword lists.
 */

import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'
import { isGameLive as isGameLiveUtil, isGameFinal as isGameFinalUtil } from '../utils/gameStatus.js'

const props = defineProps({
  card:       { type: Object, required: true },
  legs:       { type: Array,  default: () => [] },
  gamesById:  { type: Object, default: () => ({}) },
})

const router = useRouter()

function openPlayer(playerId) {
  if (!playerId) return
  router.push({ name: 'player', params: { playerId } })
}

// ── Game-status thin wrappers around the shared util ──
// CardsView duplicates this logic too; both files now route through
// src/utils/gameStatus.js so the keyword lists stay in lockstep.
function isGameLive(gameId)  { return isGameLiveUtil(props.gamesById, gameId) }
function isGameFinal(gameId) { return isGameFinalUtil(props.gamesById, gameId) }

function legStatusIndicator(leg) {
  if (leg.status === 'win')  return { kind: 'hit',     label: 'HIT'   }
  if (leg.status === 'loss') return { kind: 'busted',  label: 'MISS'  }
  if (leg.status === 'void') return { kind: 'void',    label: 'VOID'  }
  if (isGameLive(leg.game_id))  return { kind: 'live',     label: 'LIVE'  }
  if (isGameFinal(leg.game_id)) return { kind: 'awaiting', label: 'GRADING' }
  return { kind: 'pregame', label: 'PREGAME' }
}

// Compute indicators once per render keyed by leg.id — template was
// calling legStatusIndicator twice per leg (once for kind, once for
// label). For a 4-leg parlay that's 8 calls every reactive update.
const legIndicators = computed(() => {
  const map = {}
  for (const leg of props.legs) {
    map[leg.id] = legStatusIndicator(leg)
  }
  return map
})

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

// Market-bucket badge (migration 29's card_market). Reuses the per-leg market
// color palette: HR red / HRR orange / HITS green; MIX gets lab-teal (the
// site complement) since it's an intentional cross-market product, not gray
// "unknown". Returns null for legacy NULL card_market → no badge rendered.
function cardMarketBadge(m) {
  switch (m) {
    case 'hr':   return { label: 'HR',   cls: 'market-hr' }
    case 'hrr':  return { label: 'HRR',  cls: 'market-hrr-mid' }
    case 'hits': return { label: 'HITS', cls: 'market-hits' }
    case 'mix':  return { label: 'MIX',  cls: 'market-mix' }
    default:     return null
  }
}
const marketBadge = computed(() => cardMarketBadge(props.card.card_market))

// Per-card tier pill (leg count). Plain gray text, not a colored chip — it's a
// detail, not a category. Now that sections group by market, this surfaces the
// leg count per card. STRAIGHT for single-leg, N-LEG otherwise.
const tierPill = computed(() => {
  const n = props.card.leg_count
  if (n === 1) return 'STRAIGHT'
  if (n >= 2)  return `${n}-LEG`
  return null
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
  if (market === 'hr_0.5')     return 'HR'  // legacy POD market name
  if (market === 'hits_yes_1.5') return '2+ Hits'
  if (market === 'hits_yes')   return '1+ Hits'
  if (market === 'rbi_yes')    return `RBI O${line ?? 0.5}`
  if (market && market.startsWith('h_r_rbi_')) {
    return `H+R+RBI O${market.replace('h_r_rbi_', '')}`
  }
  return market || '?'
}

// Color-code market labels so users can scan a card and immediately see
// what types of bets are stacked. Heat-map intuition: green = safest
// ("will hit"), through yellow/orange = medium, red = HR longshot.
function marketColorClass(market) {
  if (market === 'hr_anytime')    return 'market-hr'
  if (market === 'hr_0.5')        return 'market-hr'  // legacy POD market name
  if (market === 'hits_yes_1.5')  return 'market-hits'   // 2+ hits — same family as 1+
  if (market === 'hits_yes')      return 'market-hits'
  if (market === 'rbi_yes')       return 'market-rbi'
  if (market === 'h_r_rbi_1.5')   return 'market-hrr-low'
  if (market === 'h_r_rbi_2.5')   return 'market-hrr-mid'
  if (market === 'h_r_rbi_3.5')   return 'market-hrr-high'
  if (market && market.startsWith('h_r_rbi_')) return 'market-hrr-mid'
  return 'market-default'
}

// Per-leg suggested stake tier (Phase 1 advisory, migration 28). Conviction
// ramp: lock (highest) → donation (barely a play). Two greens for the
// positive-conviction tiers (lock/safe, split by saturation), break to amber at
// risky (positive → uncertain), gray for lottery/donation. NULL tier (legacy
// pre-Phase 1 legs) → null → no badge.
function stakeTierBadge(tier) {
  switch (tier) {
    case 'lock':     return { label: 'LOCK',     cls: 'tier-badge--lock' }
    case 'safe':     return { label: 'SAFE',     cls: 'tier-badge--safe' }
    case 'risky':    return { label: 'RISKY',    cls: 'tier-badge--risky' }
    case 'lottery':  return { label: 'LOTTERY',  cls: 'tier-badge--lottery' }
    case 'donation': return { label: 'DONATION', cls: 'tier-badge--donation' }
    default:         return null
  }
}

// Precompute once per leg keyed by leg.id — mirrors legIndicators above so the
// template doesn't call stakeTierBadge() multiple times per leg per render.
const tierBadges = computed(() => {
  const map = {}
  for (const leg of props.legs) map[leg.id] = stakeTierBadge(leg.suggested_stake_tier)
  return map
})
</script>

<template>
  <div class="card-frame">
    <!-- Header -->
    <div class="card-header">
      <div class="flex items-baseline gap-2 flex-wrap min-w-0">
        <span class="card-label">{{ card.label || 'Card' }}</span>
        <span v-if="marketBadge" class="market-badge" :class="marketBadge.cls">{{ marketBadge.label }}</span>
        <span v-if="tierPill" class="tier-pill">{{ tierPill }}</span>
        <span v-if="card.ev_per_dollar != null" class="card-ev"
              :class="card.ev_per_dollar >= 0.1 ? 'text-signal-400' : (card.ev_per_dollar >= 0 ? 'text-fg-500' : 'text-edge-cold-1')">
          EV {{ card.ev_per_dollar >= 0 ? '+' : '' }}{{ (card.ev_per_dollar * 100).toFixed(0) }}%
        </span>
      </div>
      <span class="badge shrink-0 ml-2" :class="`badge-${cardStatusBadge.kind}`">
        {{ cardStatusBadge.label }}
      </span>
    </div>

    <!-- Legs -->
    <div>
      <div v-for="leg in legs" :key="leg.id"
           class="leg-row"
           :class="{ 'leg-row--disabled': !leg.player_id }"
           @click="openPlayer(leg.player_id)">
        <img v-if="leg.player_mlbam_id"
             :src="playerHeadshotUrl(leg.player_mlbam_id)"
             :alt="leg.player_name"
             class="leg-headshot"
             loading="lazy"
             @error="hideOnError" />
        <span v-else class="leg-headshot leg-headshot--fallback" aria-hidden="true">
          <span class="font-mono text-[10px] text-fg-500">?</span>
        </span>
        <div class="leg-body">
          <div class="leg-name">{{ leg.player_name }}</div>
          <div class="leg-meta">
            <span class="label-bracket text-signal-400">{{ leg.team_abbrev }}</span>
            <span class="text-fg-500 italic">vs</span>
            <span class="label-bracket text-fg-600">{{ leg.opponent_abbrev }}</span>
            <span class="leg-market" :class="marketColorClass(leg.market)">{{ marketLabel(leg.market, leg.line) }}</span>
            <span v-if="tierBadges[leg.id]" class="tier-badge" :class="tierBadges[leg.id].cls">
              {{ tierBadges[leg.id].label }}
            </span>
          </div>
          <div class="leg-numbers">
            <span class="leg-proj">{{ fmtPct(leg.projected_prob) }} proj</span>
            <span class="leg-odds">{{ fmtOdds(leg.american_odds) }}</span>
            <span class="leg-edge"
                  :class="leg.edge != null && Number(leg.edge) >= 0 ? 'text-signal-400' : 'text-fg-500'">
              <template v-if="leg.edge != null && Number.isFinite(Number(leg.edge))">{{ Number(leg.edge) >= 0 ? '+' : '' }}{{ fmtPct(leg.edge) }} edge</template>
              <template v-else>— edge</template>
            </span>
          </div>
        </div>
        <!-- Compute the indicator once per leg (was called twice — kind + label) -->
        <span class="leg-status" :class="`leg-status-${legIndicators[leg.id].kind}`">
          <span class="status-dot"></span>
          {{ legIndicators[leg.id].label }}
        </span>
      </div>
    </div>

    <!-- Footer: stake → payout line -->
    <div class="card-footer">
      <span class="text-fg-500">{{ fmtMoney(card.stake_rec) }} stake</span>
      <span class="text-fg-500">·</span>
      <span class="text-fg-500">{{ fmtOdds(card.combined_odds) }}</span>
      <span class="text-fg-500">·</span>
      <span v-if="card.status === 'win'" class="text-signal-400 display-num">
        cashed {{ fmtMoney(card.payout, true) }}
      </span>
      <span v-else-if="card.status === 'loss'" class="text-edge-cold-1 display-num">
        busted {{ fmtMoney(card.payout, true) }}
      </span>
      <span v-else-if="card.status === 'void'" class="text-fg-500 display-num">
        voided
      </span>
      <span v-else class="text-fg-700 display-num">
        → {{ fmtMoney(card.payout_if_hit) }} if hits
      </span>
    </div>
  </div>
</template>

<style scoped>
/* CSS mirrors the styles in CardsView.vue's <style scoped> block so the
   visual treatment stays consistent (CardBlock renders today's cards;
   CardsView's history table renders settled PODs as straight rows).
   The game-status JS logic was deduped into src/utils/gameStatus.js
   (CB-10); CSS is harder to share because Vue scoped styles are
   component-local. If you tweak market colors or leg-status badges
   here, also update CardsView. */
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

/* Market-bucket chip in the card header. Chip styling cloned from .leg-market
   (border = currentColor so it inherits the market color class); the header's
   flex `gap` handles spacing, so no margin-left here. Color + background come
   from the reused .market-* classes (.market-mix added below). */
.market-badge {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid currentColor;
}

/* Per-card leg-count detail. Plain gray text (no border/bg) so it reads as a
   sub-detail, clearly distinct from the colored .market-badge chip. */
.tier-pill {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.4);
}

.card-footer {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
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
/* When a leg has no player_id, openPlayer() noops — remove the click
   affordance so users don't tap expecting navigation. */
.leg-row--disabled {
  cursor: default;
}
.leg-row--disabled:hover { opacity: 1; }
.leg-row + .leg-row { border-top: 1px dashed rgba(255, 255, 255, 0.06); }

.leg-headshot {
  width: 44px;
  height: 44px;
  object-fit: cover;
  border-radius: 50%;
  border: 1px solid var(--bg-300, #26262c);
  flex-shrink: 0;
}
/* Fallback when leg.player_mlbam_id is null — keeps row layout consistent.
   Same size and border as the real headshot. */
.leg-headshot--fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.03);
  border-style: dashed;
}

.leg-body { flex: 1; min-width: 0; }
.leg-name {
  /* IBM Plex Sans is the site body font (style.css). Inter was specified
     here but never loaded by index.html, so this was silently falling
     back to the system sans-serif and breaking visual cohesion with
     the rest of the cards section. */
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
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
/* Market color heat-map: green = safest, yellow/orange = medium,
   red = HR longshot. Borders + dim background give a chip feel without
   overpowering the other leg details. */
.market-hits {
  color: #4ade80;  /* signal green — "will hit" */
  background: rgba(74, 222, 128, 0.08);
}
.market-rbi {
  color: #c084fc;  /* purple — RBI specialty */
  background: rgba(192, 132, 252, 0.08);
}
.market-hrr-low {
  color: #fbbf24;  /* amber — 1.5 line, medium */
  background: rgba(251, 191, 36, 0.08);
}
.market-hrr-mid {
  color: #fb923c;  /* orange — 2.5 line, longer */
  background: rgba(251, 146, 60, 0.08);
}
.market-hrr-high {
  color: #f87171;  /* light red — 3.5 line, lottery */
  background: rgba(248, 113, 113, 0.08);
}
.market-hr {
  color: #ef4444;  /* red — HR anytime, longest */
  background: rgba(239, 68, 68, 0.10);
}
.market-default {
  color: rgba(255, 255, 255, 0.55);
  background: rgba(255, 255, 255, 0.05);
}
.market-mix {
  color: #5F9EA0;  /* lab teal — cross-market product, the site complement */
  background: rgba(95, 158, 160, 0.10);
}
/* Per-leg suggested stake tier (Phase 1 advisory). Conviction ramp: two greens
   for lock/safe (split by saturation), amber break at risky, gray for
   lottery/donation (donation dimmed). Base chip cloned from .market-badge; hex
   literals per CLAUDE.md lesson #7 (Tailwind tokens flake in scoped styles). */
.tier-badge {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid currentColor;
}
.tier-badge--lock     { color: #22c55e; background: rgba(34, 197, 94, 0.14); }
.tier-badge--safe     { color: #4ade80; background: rgba(74, 222, 128, 0.07); }
.tier-badge--risky    { color: #fbbf24; background: rgba(251, 191, 36, 0.10); }
.tier-badge--lottery  { color: rgba(255, 255, 255, 0.55); background: rgba(255, 255, 255, 0.05); }
.tier-badge--donation { color: rgba(255, 255, 255, 0.30); background: rgba(255, 255, 255, 0.02); opacity: 0.7; }
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
