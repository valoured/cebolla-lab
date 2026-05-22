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
import { useFavorites } from '../composables/useFavorites.js'
import { useTodaysPOD } from '../composables/useTodaysPOD.js'
import {
  formatScore,
  formatTrend,
  scoreColorClass,
} from '../composables/useContactScore.js'
import InfoTooltip from './InfoTooltip.vue'

const { isPlayerFav } = useFavorites()
const { isPOD } = useTodaysPOD()

const props = defineProps({
  row:        { type: Object, required: true },
  marketMode: { type: String, default: 'hr' },
  hrrLine:    { type: Number, default: 1.5 },
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
  // Fallback to L14 per-PA stat when projection is missing.
  // HR → HR rate, Hits → Hit rate, HRR → no clean fallback (composite stat),
  // RBI → no projections support (UI hides this market in HRReportView).
  let fallback = null
  if (props.marketMode === 'hits') fallback = props.row.hits_per_pa
  else if (props.marketMode === 'hr') fallback = props.row.hr_pct
  // hrr / rbi → fallback stays null
  return {
    value: fallback != null ? fallback.toFixed(1) + '%' : '—',
    tone: hrPctTone(fallback),
  }
})

const marketLabel = computed(() => {
  if (props.marketMode === 'hr')   return 'HR'
  if (props.marketMode === 'hits') return 'O 0.5 Hits'
  if (props.marketMode === 'hrr')  return `O ${props.hrrLine.toFixed(1)} H+R+RBI`
  return 'O 0.5 RBI'
})
</script>

<template>
  <div class="border-b border-bg-200/40 last:border-0">
    <!-- Primary row: tap-to-expand.
         Uses a div with role="button" instead of a real <button> so that
         the nested router-link tap targets (headshot, name) are valid HTML —
         <a> can't nest inside <button>. Keyboard a11y preserved with
         tabindex + keydown handler. -->
    <div
      role="button"
      tabindex="0"
      @click="toggle"
      @keydown.enter.prevent="toggle"
      @keydown.space.prevent="toggle"
      class="w-full text-left px-3 py-2.5 flex items-center gap-3 hover:bg-bg-100/40 transition-colors cursor-pointer focus:outline-none focus-visible:bg-bg-100/40"
      :aria-expanded="expanded"
      :aria-label="`${row.name} — tap to ${expanded ? 'collapse' : 'expand'}`"
    >
      <!-- Order # -->
      <span class="display-num text-xs text-fg-500 shrink-0 w-4">{{ row.batting_order }}</span>

      <!-- Headshot is a separate tap target → player profile.
           @click.stop so the surrounding button's toggle() doesn't fire. -->
      <router-link
        :to="{ name: 'player', params: { playerId: row.player_id } }"
        class="player-link"
        :aria-label="`Open ${row.name} profile`"
        @click.stop
      >
        <img
          v-if="row.mlbam_id"
          :src="playerHeadshotUrl(row.mlbam_id)"
          :alt="row.name"
          class="player-headshot"
          loading="lazy"
          @error="hideOnError"
        />
        <span v-else class="player-headshot player-headshot--fallback">
          <span class="font-mono text-[10px] text-fg-500">→</span>
        </span>
      </router-link>

      <!-- Name + bats -->
      <div class="flex-1 min-w-0">
        <div class="flex items-baseline gap-1.5">
          <!-- Name is also a tap target → player profile.
               Inline so the card's toggle still fires for the rest of the row. -->
          <router-link
            :to="{ name: 'player', params: { playerId: row.player_id } }"
            class="player-name-link truncate"
            @click.stop
          >
            {{ row.name }}
          </router-link>
          <span
            v-if="isPOD(row.player_id)"
            class="display-num text-[8px] font-bold px-1 py-0.5 rounded-sm bg-amber-400/20 text-amber-300 border border-amber-400/40 leading-none shrink-0"
            title="Today's Play of the Day"
            aria-label="Today's Play of the Day"
          >★ POD</span>
          <span
            v-if="isPlayerFav(row.player_id)"
            class="fav-row-marker shrink-0"
            title="Favorite player"
            aria-label="Favorite player"
          >★</span>
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

      <!-- Contact score + trend (composite contact signal) -->
      <div class="shrink-0 text-right">
        <template v-if="row.contact_score != null">
          <div
            class="display-num text-[11px] font-medium inline-flex items-baseline gap-0.5 justify-end"
            :title="`${formatScore(row.contact_score)}/100 contact score (L14 percentile vs all qualified MLB batters)`"
          >
            <span :class="scoreColorClass(row.contact_score)">{{ formatScore(row.contact_score) }}</span>
            <span
              v-if="formatTrend(row.contact_trend).show"
              class="!text-[8px] font-mono"
              :class="formatTrend(row.contact_trend).direction === 'up' ? 'text-signal-400' : 'text-edge-cold-1'"
            >
              {{ formatTrend(row.contact_trend).direction === 'up' ? '▲' : '▼' }}{{ formatTrend(row.contact_trend).magnitude }}
            </span>
          </div>
          <div class="label-caps !text-[7px] mt-0.5">contact</div>
        </template>
        <template v-else>
          <div class="display-num text-xs text-fg-400">—</div>
          <div class="label-caps !text-[7px] mt-0.5 opacity-60">contact</div>
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
    </div>

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
              <template v-if="marketMode === 'hr'">BvP HR/PA</template>
              <template v-else-if="marketMode === 'hits'">BvP H/PA</template>
              <template v-else>BvP AVG</template>
              <InfoTooltip term="bvp" />
            </span>
            <span class="display-num text-xs text-fg-600">
              <template v-if="row.bvp">
                <template v-if="marketMode === 'hr'">{{ row.bvp.hr }}/{{ row.bvp.pa }}</template>
                <template v-else-if="marketMode === 'hits'">{{ row.bvp.hits }}/{{ row.bvp.pa }}</template>
                <template v-else>{{ row.bvp.avg != null ? Number(row.bvp.avg).toFixed(3).replace(/^0/, '') : '—' }}</template>
              </template>
              <template v-else>—</template>
            </span>
          </div>
          <div v-if="row.position" class="flex flex-col gap-0.5">
            <span class="label-caps !text-[7px]">Pos</span>
            <span class="display-num text-xs text-fg-600">{{ row.position }}</span>
          </div>
        </div>
        <button
          @click.stop="emit('log-bet', { player: { id: row.player_id, name: row.name }, proj: row.proj, marketMode, hrrLine })"
          class="log-btn"
          :title="row.odds ? 'Log a bet on this player' : 'Log a bet (no DK odds yet)'"
        >LOG</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Tap target wrapping the headshot — when tapped, navigates to the
   player profile. @click.stop on the link prevents the outer button's
   expand-toggle from firing on the same tap. */
.player-link {
  display: inline-flex;
  flex-shrink: 0;
  border-radius: 50%;
  outline: none;
  transition: transform 120ms ease;
}
.player-link:active {
  transform: scale(0.94);
}
.player-link:focus-visible {
  box-shadow: 0 0 0 2px rgba(255, 42, 42, 0.55);
}
.player-link:hover .player-headshot {
  filter: grayscale(0) brightness(1.10);
  border-color: rgba(255, 42, 42, 0.40);
}

/* Fallback "headshot" when no mlbam_id — keeps the tap target visible */
.player-headshot--fallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 42, 42, 0.05);
  border: 1px dashed rgba(255, 42, 42, 0.30);
}

/* Player name is also a tap target → profile. Visual hint on hover/active
   so users know it's clickable, but it stays styled like the surrounding text. */
.player-name-link {
  color: rgba(255, 255, 255, 0.85);
  text-decoration: none;
  font-size: 14px;
  line-height: 1.2;
  transition: color 120ms ease;
  min-width: 0;
}
.player-name-link:hover,
.player-name-link:active {
  color: rgba(255, 42, 42, 0.95);
  text-decoration: underline;
}

/* Inline star for favorited players. Same treatment as BatterTable. */
.fav-row-marker {
  font-size: 10px;
  line-height: 1;
  color: #FFD23F;
  filter: drop-shadow(0 0 2px rgba(255, 210, 63, 0.5));
  user-select: none;
  margin-left: -2px;
}

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
