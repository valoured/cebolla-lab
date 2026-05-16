<script setup>
import { computed } from 'vue'

const props = defineProps({
  game: { type: Object, required: true },
})

const time = computed(() => {
  if (!props.game.game_time_utc) return '—'
  const d = new Date(props.game.game_time_utc)
  return d.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })
})

const hrFactor = computed(() => {
  const v = props.game.hr_factor_overall
  if (v == null) return null
  return Number(v).toFixed(2)
})

const hrFactorTone = computed(() => {
  const v = Number(props.game.hr_factor_overall)
  if (!v) return 'text-fg-400'
  if (v >= 1.10) return 'text-signal-400'
  if (v >= 1.03) return 'text-signal-200'
  if (v <= 0.92) return 'text-edge-cold-1'
  if (v <= 0.97) return 'text-edge-cold-2'
  return 'text-fg-600'
})

const tempDisplay = computed(() => {
  if (props.game.temp_f == null) return '—'
  return `${Math.round(props.game.temp_f)}°`
})

const windDisplay = computed(() => {
  if (!props.game.wind_label) return '—'
  if (props.game.wind_label === 'dome') return 'dome'
  return `${Math.round(props.game.wind_mph || 0)}mph ${props.game.wind_label.replace('out to ', '↗ ').replace('in from ', '↙ ')}`
})

const windTone = computed(() => {
  const lbl = props.game.wind_label || ''
  if (lbl.includes('out')) return 'text-signal-200'
  if (lbl.includes('in')) return 'text-edge-cold-1'
  return 'text-fg-500'
})

const statusBadge = computed(() => {
  const s = (props.game.status || '').toLowerCase()
  if (s.includes('progress')) return { text: 'live', color: 'text-signal-400 bg-signal-400/10' }
  if (s.includes('final') || s.includes('game over')) return { text: 'final', color: 'text-fg-400 bg-bg-200/40' }
  if (s.includes('pre')) return { text: 'soon', color: 'text-fg-500 bg-bg-200/40' }
  return null
})
</script>

<template>
  <router-link
    :to="{ name: 'hr-report', params: { gameId: game.id } }"
    class="block group reticle-card"
  >
    <article
      class="bg-bg-50 border border-bg-200 hover:border-signal-400/50
             hover:bg-bg-100 transition-all duration-200 h-full
             flex flex-col"
    >
      <!-- Header strip: time, status, venue -->
      <div class="px-3 py-2 border-b border-bg-200 flex items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="display-num text-[11px] text-fg-500">{{ time }}</span>
          <span
            v-if="statusBadge"
            class="text-[9px] uppercase tracking-wide2 font-mono px-1.5 py-0.5 rounded-sm"
            :class="statusBadge.color"
          >
            {{ statusBadge.text }}
          </span>
        </div>
        <span class="label-caps !text-[9px] truncate max-w-[140px]" :title="game.venue || game.home_team?.stadium">
          {{ game.venue || game.home_team?.stadium }}
        </span>
      </div>

      <!-- Matchup -->
      <div class="px-3 py-3 flex-1">
        <div class="grid grid-cols-[1fr_auto_1fr] items-center gap-3 mb-3">
          <!-- Away -->
          <div class="text-right">
            <div class="display-text text-lg text-fg-700 leading-tight tracking-tight">
              {{ game.away_team?.abbrev || '—' }}
            </div>
            <div class="text-[10px] text-fg-500 mt-0.5 truncate">
              {{ game.away_pitcher?.name || 'TBD' }}
            </div>
          </div>

          <div class="text-fg-400 text-xs">@</div>

          <!-- Home -->
          <div class="text-left">
            <div class="display-text text-lg text-fg-700 leading-tight tracking-tight">
              {{ game.home_team?.abbrev || '—' }}
            </div>
            <div class="text-[10px] text-fg-500 mt-0.5 truncate">
              {{ game.home_pitcher?.name || 'TBD' }}
            </div>
          </div>
        </div>

        <!-- Stats strip: HR factor, temp, wind -->
        <div class="grid grid-cols-3 gap-2 pt-2 border-t border-bg-200">
          <div class="flex flex-col">
            <span class="label-caps !text-[8px]">HRF</span>
            <span class="display-num text-sm mt-0.5" :class="hrFactorTone">
              {{ hrFactor ?? '—' }}
            </span>
          </div>
          <div class="flex flex-col">
            <span class="label-caps !text-[8px]">Temp</span>
            <span class="display-num text-sm mt-0.5 text-fg-600">
              {{ tempDisplay }}
            </span>
          </div>
          <div class="flex flex-col min-w-0">
            <span class="label-caps !text-[8px]">Wind</span>
            <span class="display-num text-[11px] mt-1 truncate" :class="windTone" :title="windDisplay">
              {{ windDisplay }}
            </span>
          </div>
        </div>
      </div>

      <!-- Hover arrow on right side (subtle) -->
      <div class="px-3 pb-2 flex justify-end">
        <span class="label-caps !text-[9px] group-hover:text-signal-400 transition opacity-60 group-hover:opacity-100">
          open →
        </span>
      </div>
    </article>
  </router-link>
</template>
