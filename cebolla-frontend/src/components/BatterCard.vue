<script setup>
/**
 * BatterCard.vue — mobile card-per-batter view.
 *
 * Used by BatterTable as the < md viewport rendering. Shows the primary
 * betting signal (Odds + Edge + Proj%) prominently, with a tap-to-expand
 * reveal for the secondary stats (HH%, Brl%, xSLG, xBA, BvP).
 *
 * Designed for parlay-builder workflow: scan rapidly, expand a couple of
 * hitters that pique interest, log a bet.
 */

import { ref, computed } from 'vue'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'
import { statColor, fmtStat } from '../utils/percentileColors.js'
import InfoTooltip from './InfoTooltip.vue'

const props = defineProps({
  row:        { type: Object, required: true },
  marketMode: { type: String, default: 'hr' },
})

const emit = defineEmits(['log-bet'])

const expanded = ref(false)
function toggle() { expanded.value = !expanded.value }

function fmtAmerican(n) {
  if (n == null) return null
  return n > 0 ? `+${n}` : `${n}`
}

function hrPctTone(pct) {
  if (pct == null) return 'text-fg-500'
  if (pct >= 5) return 'text-signal-400'
  if (pct >= 3.5) return 'text-signal-200'
  if (pct >= 2) return 'text-fg-600'
  return 'text-edge-cold-1'
}

const edgePill = computed(() => {
  const edge = props.row.proj?.edge
  if (edge == null) return null
  const pct = edge * 100
  let cls = 'text-fg-500 bg-bg-200/40'
  if (pct >= 5)       cls = 'text-signal-400 bg-signal-400/15'
  else if (pct >= 2)  cls = 'text-signal-200 bg-signal-400/8'
  else if (pct >= -2) cls = 'text-fg-500 bg-bg-200/40'
  else if (pct >= -5) cls = 'text-edge-cold-2 bg-edge-cold-2/8'
  else                cls = 'text-edge-cold-1 bg-edge-cold-1/15'
  const sign = pct >= 0 ? '+' : ''
  return { text: `${sign}${pct.toFixed(1)}%`, cls }
})

const projDisplay = computed(() => {
  const proj = props.row.proj
  if (proj?.projected_prob != null) {
    return {
      value: (proj.projected_prob * 100).toFixed(1) + '%',
      tone: hrPctTone(proj.projected_prob * 100),
    }
  }
  const fallback = props.marketMode === 'hits' ? props.row.hits_per_pa : props.row.hr_pct
  return {
    value: fallback != null ? fallback.toFixed(1) + '%' : '—',
    tone: hrPctTone(fallback),
  }
})

const marketLabel = computed(() => {
  if (props.marketMode === 'hr')   return 'HR'
  if (props.marketMode === 'hits') return 'O 0.5 Hits'
  return 'O 0.5 RBI'
})
</script>

<template>
  <div class="border-b border-bg-200/40 last:border-0">
    <!-- Primary row: tap-to-expand -->
    <button
      type="button"
      @click="toggle"
      class="w-full text-left px-3 py-2.5 flex items-center gap-3 hover:bg-bg-100/40 transition-colors"
    >
      <!-- Order # -->
      <span class="display-num text-xs text-fg-500 shrink-0 w-4">{{ row.batting_order }}</span>

      <!-- Headshot -->
      <img
        v-if="row.mlbam_id"
        :src="playerHeadshotUrl(row.mlbam_id)"
        :alt="row.name"
        class="player-headshot"
        loading="lazy"
        @error="hideOnError"
      />

      <!-- Name + bats -->
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline gap-1.5">
          <span class="text-fg-700 text-sm truncate">{{ row.name }}</span>
          <span class="font-mono text-[9px] text-fg-500 shrink-0">{{ row.bats || '?' }}</span>
        </div>
        <div class="flex items-baseline gap-2 mt-0.5">
          <!-- Odds -->
          <span
            v-if="row.odds"
            class="display-num text-xs font-medium"
            :class="row.odds.american_odds < 0 ? 'text-signal-200' : 'text-fg-700'"
          >
            {{ fmtAmerican(row.odds.american_odds) }}
          </span>
          <span v-else class="display-num text-xs text-fg-400">—</span>
          <span class="label-caps !text-[8px]">{{ marketLabel }}</span>
        </div>
      </div>

      <!-- Edge pill (primary signal) -->
      <div class="shrink-0 text-right">
        <template v-if="edgePill">
          <div class="display-num text-[11px] px-1.5 py-0.5 rounded-sm font-medium inline-block"
               :class="edgePill.cls">
            {{ edgePill.text }}
          </div>
          <div class="label-caps !text-[7px] mt-0.5">edge</div>
        </template>
        <template v-else-if="row.proj?.edge_bucket === 'longshot_unrated'">
          <div class="label-bracket !text-[8px] opacity-40">longshot</div>
        </template>
        <template v-else>
          <div class="label-bracket !text-[8px] opacity-50">
            {{ marketMode === 'hr' ? 'no data' : 'pending' }}
          </div>
        </template>
      </div>

      <!-- Proj% (secondary key signal) -->
      <div class="shrink-0 text-right w-12">
        <div class="display-num text-sm font-medium" :class="projDisplay.tone">
          {{ projDisplay.value }}
        </div>
        <div class="label-caps !text-[7px] mt-0.5">proj</div>
      </div>

      <!-- Expand chevron -->
      <span
        class="shrink-0 text-fg-500 text-xs transition-transform duration-200"
        :class="{ 'rotate-180': expanded }"
      >▾</span>
    </button>

    <!-- Expanded detail (4 Statcast cells + BvP + LOG) -->
    <div v-if="expanded" class="px-3 pb-3 pt-1 bg-bg-50/60 border-t border-bg-200/40">
      <!-- Statcast 2x2 grid -->
      <div class="grid grid-cols-4 gap-2 mb-3">
        <div class="flex flex-col gap-0.5">
          <span class="label-caps !text-[7px] inline-flex items-center">
            HH% <InfoTooltip term="hard_hit_pct" />
          </span>
          <span
            class="display-num text-sm"
            :class="statColor(row.hard_hit_pct, 'hard_hit_pct', 'batter')"
          >
            {{ fmtStat(row.hard_hit_pct, 'hard_hit_pct') }}
          </span>
        </div>
        <div class="flex flex-col gap-0.5">
          <span class="label-caps !text-[7px] inline-flex items-center">
            Brl% <InfoTooltip term="barrel_pct" />
          </span>
          <span
            class="display-num text-sm"
            :class="statColor(row.barrel_pct, 'barrel_pct', 'batter')"
          >
            {{ fmtStat(row.barrel_pct, 'barrel_pct') }}
          </span>
        </div>
        <div class="flex flex-col gap-0.5">
          <span class="label-caps !text-[7px] inline-flex items-center">
            xSLG <InfoTooltip term="xslg" />
          </span>
          <span
            class="display-num text-sm"
            :class="statColor(row.xslg, 'xslg', 'batter')"
          >
            {{ fmtStat(row.xslg, 'xslg') }}
          </span>
        </div>
        <div class="flex flex-col gap-0.5">
          <span class="label-caps !text-[7px] inline-flex items-center">
            xBA <InfoTooltip term="xba" />
          </span>
          <span
            class="display-num text-sm"
            :class="statColor(row.xba, 'xba', 'batter')"
          >
            {{ fmtStat(row.xba, 'xba') }}
          </span>
        </div>
      </div>

      <!-- BvP + LOG row -->
      <div class="flex items-center justify-between gap-3">
        <div class="flex items-center gap-3">
          <div class="flex flex-col gap-0.5">
            <span class="label-caps !text-[7px] inline-flex items-center">
              BvP HR/PA <InfoTooltip term="bvp" />
            </span>
            <span class="display-num text-xs text-fg-600">
              <template v-if="row.bvp">{{ row.bvp.hr }}/{{ row.bvp.pa }}</template>
              <template v-else>—</template>
            </span>
          </div>
          <div v-if="row.position" class="flex flex-col gap-0.5">
            <span class="label-caps !text-[7px]">Pos</span>
            <span class="display-num text-xs text-fg-600">{{ row.position }}</span>
          </div>
        </div>
        <button
          @click.stop="emit('log-bet', { player: { id: row.player_id, name: row.name }, proj: row.proj, marketMode })"
          class="log-btn"
          :title="row.odds ? 'Log a bet on this player' : 'Log a bet (no DK odds yet)'"
        >LOG</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.player-headshot {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  filter: grayscale(0.35) brightness(0.95);
  opacity: 0.85;
}

.log-btn {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  padding: 4px 10px;
  border: 1px solid rgba(255,42,42,0.30);
  background: transparent;
  color: rgba(255,42,42,0.85);
  border-radius: 2px;
  transition: all 0.12s;
}
.log-btn:active {
  border-color: var(--color-accent-red, #FF2A2A);
  color: var(--color-accent-red, #FF2A2A);
  background: rgba(255,42,42,0.12);
}
</style>
