<script setup>
/**
 * PitcherAllowedStats.vue — mobile-responsive.
 *
 * Grid: 2 cols on mobile, 4 cols from sm up.
 * Toggle stays the same.
 */

import { computed, toRef } from 'vue'
import { useStatcastPitcher } from '../composables/useStatcast.js'
import { statColor, fmtStat } from '../utils/percentileColors.js'
import StatcastWindowToggle from './StatcastWindowToggle.vue'

const props = defineProps({
  pitcherId: { type: Number, default: null },
})

const pitcherIdRef = toRef(props, 'pitcherId')
const {
  stats,
  windowType,
  setWindow,
  loading,
} = useStatcastPitcher(pitcherIdRef, 'l14')

const currentWindow = computed({
  get: () => windowType.value,
  set: (v) => setWindow(v),
})

function num(v) {
  if (v == null) return null
  const n = Number(v)
  return isNaN(n) ? null : n
}
</script>

<template>
  <div v-if="pitcherId" class="px-3 sm:px-4 py-2.5 border-t border-bg-200 bg-bg-50/50">
    <!-- Header row: stacks on very small screens, side-by-side from xs up -->
    <div class="flex items-center justify-between gap-3 mb-2 flex-wrap">
      <span class="label-caps">Allowed (Statcast)</span>
      <div class="flex items-center gap-2">
        <span v-if="loading" class="label-caps !text-[8px] text-fg-400 italic">loading…</span>
        <StatcastWindowToggle v-model="currentWindow" />
      </div>
    </div>

    <!-- 2 cols mobile, 4 cols from sm -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <div class="flex flex-col gap-0.5">
        <span class="label-caps !text-[8px]" title="Hard-Hit % (95+ mph) allowed">HH% Allowed</span>
        <span
          class="display-num text-base font-medium"
          :class="statColor(num(stats?.hard_hit_pct), 'hard_hit_pct', 'pitcher')"
        >
          {{ fmtStat(num(stats?.hard_hit_pct), 'hard_hit_pct') }}
        </span>
      </div>
      <div class="flex flex-col gap-0.5">
        <span class="label-caps !text-[8px]" title="Barrel % allowed">Brl% Allowed</span>
        <span
          class="display-num text-base font-medium"
          :class="statColor(num(stats?.barrel_pct), 'barrel_pct', 'pitcher')"
        >
          {{ fmtStat(num(stats?.barrel_pct), 'barrel_pct') }}
        </span>
      </div>
      <div class="flex flex-col gap-0.5">
        <span class="label-caps !text-[8px]" title="Expected Slugging allowed">xSLG Allowed</span>
        <span
          class="display-num text-base font-medium"
          :class="statColor(num(stats?.xslg), 'xslg', 'pitcher')"
        >
          {{ fmtStat(num(stats?.xslg), 'xslg') }}
        </span>
      </div>
      <div class="flex flex-col gap-0.5">
        <span class="label-caps !text-[8px]" title="Expected Batting Average allowed">xBA Allowed</span>
        <span
          class="display-num text-base font-medium"
          :class="statColor(num(stats?.xba), 'xba', 'pitcher')"
        >
          {{ fmtStat(num(stats?.xba), 'xba') }}
        </span>
      </div>
    </div>

    <div v-if="stats?.bbe != null" class="mt-2 flex items-baseline justify-between">
      <span class="label-caps !text-[8px] text-fg-400">
        {{ stats.bbe }} batted balls
        <template v-if="stats.window_start && currentWindow !== 'season'">
          since {{ stats.window_start }}
        </template>
      </span>
    </div>
  </div>
</template>
