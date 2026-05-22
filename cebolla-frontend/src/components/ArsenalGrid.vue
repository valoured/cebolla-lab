<script setup>
import { computed } from 'vue'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'

const props = defineProps({
  pitcher: { type: Object, default: null },
  arsenal: { type: Array, default: () => [] },
  label:   { type: String, default: 'PITCHER' },
})

const pitcherHeadshot = computed(() => {
  if (!props.pitcher?.mlbam_id) return null
  return playerHeadshotUrl(props.pitcher.mlbam_id)
})

const pitchRows = computed(() => {
  const byPitch = {}
  for (const r of props.arsenal) {
    const key = r.pitch_type
    if (!byPitch[key]) {
      byPitch[key] = {
        pitch_type: key,
        pitches_l: 0, pitches_r: 0,
        hr_l: 0, hr_r: 0,
        pa_l: 0, pa_r: 0,
        velo_sum: 0, velo_n: 0,
        barrel_pct_sum: 0, barrel_n: 0,
      }
    }
    const p = byPitch[key]
    if (r.vs_stance === 'L') {
      p.pitches_l = r.pitches || 0
      p.hr_l = r.hr || 0
      p.pa_l = r.pa || 0
    } else if (r.vs_stance === 'R') {
      p.pitches_r = r.pitches || 0
      p.hr_r = r.hr || 0
      p.pa_r = r.pa || 0
    }
    if (r.velo_avg) { p.velo_sum += r.velo_avg; p.velo_n += 1 }
    if (r.barrel_pct != null) { p.barrel_pct_sum += r.barrel_pct; p.barrel_n += 1 }
  }

  const totalPitches = Object.values(byPitch)
    .reduce((s, p) => s + p.pitches_l + p.pitches_r, 0)

  return Object.values(byPitch)
    .map(p => {
      const total = p.pitches_l + p.pitches_r
      const totalHR = p.hr_l + p.hr_r
      const totalPA = p.pa_l + p.pa_r
      const usage_l = totalPitches ? (p.pitches_l / totalPitches * 100) : 0
      const usage_r = totalPitches ? (p.pitches_r / totalPitches * 100) : 0
      return {
        pitch_type: p.pitch_type,
        usage_pct: totalPitches ? (total / totalPitches * 100) : 0,
        usage_l, usage_r,
        velo: p.velo_n ? (p.velo_sum / p.velo_n) : null,
        hr_pct: totalPA ? (totalHR / totalPA * 100) : null,
        barrel_pct: p.barrel_n ? (p.barrel_pct_sum / p.barrel_n) : null,
      }
    })
    .filter(p => p.usage_pct >= 1)
    .sort((a, b) => b.usage_pct - a.usage_pct)
})

function hrTone(pct) {
  if (pct == null) return 'text-fg-500'
  if (pct >= 4) return 'text-signal-400'
  if (pct >= 2.5) return 'text-signal-200'
  if (pct >= 1.5) return 'text-fg-600'
  return 'text-edge-cold-1'
}
</script>

<template>
  <div>
    <!-- Header -->
    <div class="px-3 sm:px-4 py-3 border-b border-bg-200 flex items-center justify-between gap-2">
      <div class="flex items-center gap-2 sm:gap-3 min-w-0">
        <span class="label-bracket text-signal-400 shrink-0">{{ label }}</span>
        <template v-if="pitcher">
          <router-link
            :to="{ name: 'player', params: { playerId: pitcher.id } }"
            class="flex items-center gap-2 min-w-0 group hover:text-signal-400 transition"
          >
            <img
              v-if="pitcherHeadshot"
              :src="pitcherHeadshot"
              :alt="pitcher.name"
              class="pitcher-headshot"
              @error="hideOnError"
            />
            <span class="display-text text-sm sm:text-base text-fg-700 group-hover:text-signal-400 truncate transition">
              {{ pitcher.name }}
            </span>
          </router-link>
        </template>
        <span v-else class="text-fg-500 text-sm italic">TBD</span>
      </div>
      <span v-if="pitcher?.throws" class="label-caps !text-[9px] shrink-0">
        Throws {{ pitcher.throws }}HP
      </span>
    </div>

    <!-- Arsenal rows: column widths tighten on mobile -->
    <div v-if="pitchRows.length" class="px-3 sm:px-4 py-2">
      <!-- Column headers -->
      <div class="grid grid-cols-[32px_1fr_38px_38px_38px] sm:grid-cols-[40px_1fr_50px_50px_50px] gap-1.5 sm:gap-2 pb-2 border-b border-bg-200">
        <span class="label-caps !text-[8px]">Pitch</span>
        <span class="label-caps !text-[8px]">Usage</span>
        <span class="label-caps !text-[8px] text-right">Velo</span>
        <span class="label-caps !text-[8px] text-right">HR%</span>
        <span class="label-caps !text-[8px] text-right">Brl%</span>
      </div>
      <!-- Rows -->
      <div
        v-for="p in pitchRows"
        :key="p.pitch_type"
        class="grid grid-cols-[32px_1fr_38px_38px_38px] sm:grid-cols-[40px_1fr_50px_50px_50px] gap-1.5 sm:gap-2 items-center py-1.5 border-b border-bg-200/50 last:border-0"
      >
        <span class="display-num text-xs text-fg-700 font-medium">{{ p.pitch_type }}</span>
        <!-- Usage bar -->
        <div class="flex items-center gap-2 min-w-0">
          <div class="flex-1 h-1.5 bg-bg-200 relative overflow-hidden">
            <div class="absolute inset-y-0 left-0 bg-signal-400/70"
                 :style="{ width: `${Math.min(p.usage_pct, 100)}%` }"></div>
          </div>
          <span class="display-num text-[10px] text-fg-500 w-6 sm:w-7 text-right">
            {{ p.usage_pct.toFixed(0) }}%
          </span>
        </div>
        <span class="display-num text-[11px] text-fg-600 text-right">
          {{ p.velo ? p.velo.toFixed(0) : '—' }}
        </span>
        <span class="display-num text-[11px] text-right" :class="hrTone(p.hr_pct)">
          {{ p.hr_pct != null ? p.hr_pct.toFixed(1) : '—' }}
        </span>
        <span class="display-num text-[11px] text-fg-600 text-right">
          {{ p.barrel_pct != null ? p.barrel_pct.toFixed(1) : '—' }}
        </span>
      </div>
    </div>

    <div v-else class="px-4 py-6 text-center text-fg-500 text-xs italic">
      no arsenal data
    </div>
  </div>
</template>

<style scoped>
.pitcher-headshot {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  filter: grayscale(0.18) brightness(0.97);
  border: 1px solid rgba(255, 255, 255, 0.06);
  transition: filter 0.2s, border-color 0.2s, transform 0.2s;
}
.group:hover .pitcher-headshot {
  filter: grayscale(0) brightness(1.05);
  border-color: rgba(255, 42, 42, 0.4);
  transform: scale(1.05);
}
</style>
