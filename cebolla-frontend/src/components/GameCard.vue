<script setup>
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { teamLogoUrl, hideOnError } from '../utils/mlbImages.js'
import { formatGameTimeShort, formatCountdown, minutesUntil } from '../utils/timeHelpers.js'

const props = defineProps({
  game: { type: Object, required: true },
})

// Refresh the clock state every 30s so countdowns tick without page reload
const tickKey = ref(0)
let tickTimer = null
onMounted(() => { tickTimer = setInterval(() => tickKey.value++, 30_000) })
onUnmounted(() => { if (tickTimer) clearInterval(tickTimer) })

const time = computed(() => {
  // tickKey reactivity prevents this from going stale; explicit reference:
  tickKey.value
  return formatGameTimeShort(props.game.game_time_utc)
})

// Show countdown when game starts within the next 2 hours
const countdown = computed(() => {
  tickKey.value
  const mins = minutesUntil(props.game.game_time_utc)
  if (mins == null) return null
  if (mins <= 0 || mins > 120) return null
  return formatCountdown(props.game.game_time_utc)
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
  if (s.includes('progress') || s.includes('manager challenge') || s.includes('replay')) {
    return { text: 'live', color: 'text-signal-400 bg-signal-400/10' }
  }
  if (s.includes('delayed') || s.includes('suspended')) {
    return { text: s.split(' ')[0].slice(0, 7), color: 'text-amber-400 bg-amber-400/10' }
  }
  if (s.includes('final') || s.includes('game over')) {
    return { text: 'final', color: 'text-fg-400 bg-bg-200/40' }
  }
  if (s.includes('postpone') || s.includes('cancel')) {
    return { text: 'ppd', color: 'text-fg-400 bg-bg-200/40' }
  }
  if (s.includes('pre') || s.includes('warmup')) {
    return { text: 'soon', color: 'text-fg-500 bg-bg-200/40' }
  }
  return null
})

const isLive = computed(() => {
  const s = (props.game.status || '').toLowerCase()
  return s.includes('progress') ||
         s.includes('manager challenge') ||
         s.includes('replay') ||
         s.includes('delayed') ||
         s.includes('suspended')
})

const isFinal = computed(() => {
  const s = (props.game.status || '').toLowerCase()
  return s.includes('final') || s.includes('game over')
})

const showScores = computed(() => {
  return (isLive.value || isFinal.value) &&
         props.game.away_score != null &&
         props.game.home_score != null
})

const inningDisplay = computed(() => {
  if (!isLive.value) return null
  const inning = props.game.current_inning
  const state = (props.game.inning_state || '').toLowerCase()
  if (!inning) return null
  const arrow = state.startsWith('top') ? '↑' :
                state.startsWith('bot') ? '↓' :
                state.startsWith('mid') ? '·' : ''
  return `${arrow}${inning}`
})

const awayLogo = computed(() => teamLogoUrl(props.game.away_team?.mlb_id))
const homeLogo = computed(() => teamLogoUrl(props.game.home_team?.mlb_id))
</script>

<template>
  <router-link
    :to="{ name: 'hr-report', params: { gameId: game.id } }"
    class="block group reticle-card"
    :class="{ 'live-pulse': isLive }"
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
          <span v-if="countdown" class="display-num text-[10px] text-signal-200 ml-0.5">
            ({{ countdown }})
          </span>
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
      <div class="px-3.5 py-4 flex-1">
        <div class="grid grid-cols-[1fr_auto_1fr] items-center gap-3 mb-3.5">
          <!-- Away -->
          <div class="text-right flex items-center justify-end gap-2">
            <span
              v-if="showScores"
              class="display-num text-2xl leading-none"
              :class="game.away_score > game.home_score ? 'text-fg-800' : 'text-fg-500'"
            >
              {{ game.away_score }}
            </span>
            <div class="min-w-0">
              <div class="display-text text-xl text-fg-700 leading-tight tracking-tight">
                {{ game.away_team?.abbrev || '—' }}
              </div>
              <div class="text-[10px] text-fg-500 mt-0.5 truncate">
                {{ game.away_pitcher?.name || 'TBD' }}
              </div>
            </div>
            <img
              v-if="awayLogo"
              :src="awayLogo"
              :alt="game.away_team?.abbrev"
              class="team-logo"
              loading="lazy"
              @error="hideOnError"
            />
          </div>

          <div class="text-center">
            <div v-if="inningDisplay" class="display-num text-[11px] text-signal-400 leading-none">
              {{ inningDisplay }}
            </div>
            <div v-else class="text-fg-400 text-xs">@</div>
          </div>

          <!-- Home -->
          <div class="text-left flex items-center gap-2">
            <img
              v-if="homeLogo"
              :src="homeLogo"
              :alt="game.home_team?.abbrev"
              class="team-logo"
              loading="lazy"
              @error="hideOnError"
            />
            <div class="min-w-0">
              <div class="display-text text-xl text-fg-700 leading-tight tracking-tight">
                {{ game.home_team?.abbrev || '—' }}
              </div>
              <div class="text-[10px] text-fg-500 mt-0.5 truncate">
                {{ game.home_pitcher?.name || 'TBD' }}
              </div>
            </div>
            <span
              v-if="showScores"
              class="display-num text-2xl leading-none"
              :class="game.home_score > game.away_score ? 'text-fg-800' : 'text-fg-500'"
            >
              {{ game.home_score }}
            </span>
          </div>
        </div>

        <!-- Stats strip: HR factor, temp, wind -->
        <div class="grid grid-cols-3 gap-3 pt-4 border-t border-bg-200">
          <div class="flex flex-col">
            <span class="label-caps !text-[10px]">HRF</span>
            <span class="display-num text-2xl mt-1.5 leading-none" :class="hrFactorTone">
              {{ hrFactor ?? '—' }}
            </span>
          </div>
          <div class="flex flex-col">
            <span class="label-caps !text-[10px]">Temp</span>
            <span class="display-num text-2xl mt-1.5 leading-none text-fg-600">
              {{ tempDisplay }}
            </span>
          </div>
          <div class="flex flex-col min-w-0">
            <span class="label-caps !text-[10px]">Wind</span>
            <span class="display-num text-base mt-1.5 leading-tight truncate" :class="windTone" :title="windDisplay">
              {{ windDisplay }}
            </span>
          </div>
        </div>
      </div>

      <!-- Hover arrow on right side (subtle) -->
      <div class="px-3.5 pb-2 flex justify-end">
        <span class="label-caps !text-[9px] group-hover:text-signal-400 transition opacity-60 group-hover:opacity-100">
          open →
        </span>
      </div>
    </article>
  </router-link>
</template>

<style scoped>
/* ── Team logos: bigger, less muted ── */
.team-logo {
  width: 32px;
  height: 32px;
  object-fit: contain;
  flex-shrink: 0;
  filter: grayscale(0.15) brightness(1.05) contrast(1.05);
  opacity: 0.95;
  transition: filter 0.2s, opacity 0.2s, transform 0.2s;
}
.group:hover .team-logo {
  filter: grayscale(0) brightness(1.1) contrast(1.05);
  opacity: 1;
  transform: scale(1.05);
}

/* ── Live game: signal-red box-shadow pulse on the article ── */
.live-pulse article {
  animation: live-glow 2.4s ease-in-out infinite;
  border-color: rgba(255, 42, 42, 0.5) !important;
  position: relative;
  z-index: 1;
}

@keyframes live-glow {
  0%, 100% {
    box-shadow: 0 0 0 0 rgba(255, 42, 42, 0.0),
                0 0 8px 0 rgba(255, 42, 42, 0.20);
  }
  50% {
    box-shadow: 0 0 0 2px rgba(255, 42, 42, 0.35),
                0 0 24px 4px rgba(255, 42, 42, 0.40);
  }
}
</style>
