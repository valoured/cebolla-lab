<script setup>
import { ref, computed, watch } from 'vue'
import { useSlate } from '../composables/useSlate.js'
import GameCard from '../components/GameCard.vue'
import LoadingBrand from '../components/LoadingBrand.vue'
import DateNav from '../components/DateNav.vue'

const {
  games,
  loading,
  error,
  activeDate,
  availableDates,
  targetDate,
  setTargetDate,
} = useSlate()

const activeFilter = ref(null)

// Track when the slate was last refreshed. Driven by the games ref —
// every time the composable reloads we get a new array reference and the
// watcher fires. Without this, lastUpdate would be evaluated once at mount
// and never update.
const lastUpdateAt = ref(new Date())
watch(games, () => { lastUpdateAt.value = new Date() })

function toggleFilter(key) {
  activeFilter.value = activeFilter.value === key ? null : key
}

// Finals get pushed to the bottom of the slate. Within each group (non-final
// vs final), the composable's existing game_time_utc ascending order is
// preserved by the stable sort.
function isFinalStatus(status) {
  const s = (status || '').toLowerCase()
  return s.includes('final') || s.includes('game over') || s.includes('completed early')
}

const filteredGames = computed(() => {
  const list = !activeFilter.value
    ? games.value
    : games.value.filter(g => {
        const hrf = Number(g.hr_factor_overall) || 0
        const status = (g.status || '').toLowerCase()
        switch (activeFilter.value) {
          case 'hot':   return hrf >= 1.05
          case 'cold':  return hrf > 0 && hrf <= 0.95
          case 'dome':  return g.wind_label === 'dome'
          case 'live':  // Match GameCard's isInProgress logic: progress / manager
                       // challenge / replay all mean "actively playing right now"
                       return status.includes('progress') ||
                              status.includes('manager challenge') ||
                              status.includes('replay')
          default:      return true
        }
      })

  // Stable partition: non-finals first, finals last.
  // .slice() to avoid mutating the source, then sort by finality only —
  // V8's sort is stable, so within-group time order survives.
  return list.slice().sort((a, b) => {
    const af = isFinalStatus(a.status) ? 1 : 0
    const bf = isFinalStatus(b.status) ? 1 : 0
    return af - bf
  })
})

const displayedDate = computed(() => {
  if (activeDate.value) {
    const [y, m, d] = activeDate.value.split('-').map(Number)
    return new Date(y, m - 1, d)
  }
  if (games.value.length > 0 && games.value[0].game_date) {
    const [y, m, d] = games.value[0].game_date.split('-').map(Number)
    return new Date(y, m - 1, d)
  }
  return new Date()
})

const dateLong = computed(() => displayedDate.value.toLocaleDateString('en-US', {
  weekday: 'long', month: 'long', day: 'numeric',
}))
const dateShort = computed(() => displayedDate.value.toLocaleDateString('en-US', {
  weekday: 'short', month: 'short', day: 'numeric',
}))

const lastUpdate = computed(() => {
  return lastUpdateAt.value.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
})

const filterChips = [
  { key: 'hot',  label: 'Hot Park',  hint: 'HRF ≥ 1.05' },
  { key: 'cold', label: 'Cold Park', hint: 'HRF ≤ 0.95' },
  { key: 'dome', label: 'Domes',     hint: 'No weather' },
  { key: 'live', label: 'Live',      hint: 'In progress' },
]

// Show DateNav only when there's more than one option to pick from.
// Otherwise it's just visual noise.
const showDateNav = computed(() => (availableDates.value?.length || 0) > 1)
</script>

<template>
  <div class="min-h-screen">
    <!-- Header: stacks vertically on mobile, side-by-side from md up -->
    <header class="px-4 sm:px-6 pt-6 sm:pt-8 pb-4 sm:pb-5 border-b border-bg-200">
      <div class="flex flex-col md:flex-row md:items-end md:justify-between gap-4 md:gap-6 mb-4">
        <div>
          <div class="flex items-center gap-3 mb-2">
            <span class="label-bracket text-signal-400">slate · MLB</span>
            <span class="label-caps">{{ dateShort }}</span>
          </div>
          <!-- Date text scales: 2xl mobile, 3xl desktop -->
          <h1 class="display-text text-2xl sm:text-3xl text-fg-800 tracking-tight leading-none">
            {{ dateLong }}
          </h1>
        </div>

        <!-- Stats strip: compact on mobile -->
        <div class="flex items-center gap-3 sm:gap-5 text-left md:text-right">
          <div>
            <div class="label-caps">Total</div>
            <div class="display-num text-xl sm:text-2xl text-fg-700 leading-none mt-1">{{ games.length }}</div>
          </div>
          <div class="w-px h-7 sm:h-8 bg-bg-200"></div>
          <div>
            <div class="label-caps">Showing</div>
            <div class="display-num text-xl sm:text-2xl text-fg-700 leading-none mt-1">{{ filteredGames.length }}</div>
          </div>
          <div class="w-px h-7 sm:h-8 bg-bg-200 hidden sm:block"></div>
          <div class="hidden sm:block">
            <div class="label-caps">Updated</div>
            <div class="display-num text-xs text-fg-500 leading-none mt-1">{{ lastUpdate }}</div>
          </div>
        </div>
      </div>

      <!-- Date nav: only shown when there's more than one slate date available -->
      <DateNav
        v-if="showDateNav"
        :dates="availableDates"
        :active-date="activeDate"
        :target-date="targetDate"
        @update:target-date="setTargetDate"
        class="mb-4"
      />

      <!-- Filter chips: wrap nicely on mobile -->
      <div class="flex items-center gap-2 flex-wrap">
        <span class="label-caps mr-1 sm:mr-2 shrink-0">filter:</span>
        <button
          v-for="chip in filterChips"
          :key="chip.key"
          @click="toggleFilter(chip.key)"
          class="text-[11px] px-2.5 py-1 border transition flex items-center gap-2"
          :class="activeFilter === chip.key
            ? 'border-signal-400 text-signal-200 bg-signal-400/10'
            : 'border-bg-200 text-fg-500 hover:border-bg-300 hover:text-fg-700'"
        >
          <span class="font-medium">{{ chip.label }}</span>
          <!-- Hint hidden on smallest screens -->
          <span class="hidden sm:inline font-mono text-[9px] opacity-70">{{ chip.hint }}</span>
        </button>
        <button
          v-if="activeFilter"
          @click="activeFilter = null"
          class="text-[11px] px-2.5 py-1 text-signal-300 hover:text-signal-200 transition ml-auto"
        >
          clear ×
        </button>
      </div>
    </header>

    <!-- Content -->
    <section class="px-4 sm:px-6 py-5 sm:py-6">
      <LoadingBrand v-if="loading" text="Peeling layers…" />

      <div v-else-if="error" class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">connection error</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>

      <div v-else-if="!games.length" class="text-center py-20">
        <img
          src="/cebolla-wordmark.png"
          alt="Cebolla"
          class="cebolla-wordmark-empty mx-auto mb-6"
        />
        <div class="display-text text-2xl text-fg-500 italic mb-3">No active slate</div>
        <div class="text-fg-500 text-sm max-w-md mx-auto leading-relaxed">
          No upcoming non-final games in the database.<br>
          <span class="text-fg-400">The next slate will appear here as soon as MLB publishes it.</span>
        </div>
        <div class="label-bracket !text-[9px] text-fg-500 mt-6">
          checked {{ lastUpdate }}
        </div>
      </div>

      <div v-else-if="!filteredGames.length" class="text-center py-20">
        <div class="display-text text-2xl text-fg-500 italic mb-2">Filtered to zero</div>
        <button
          @click="activeFilter = null"
          class="label-caps hover:text-signal-400 transition mt-2"
        >
          clear filter
        </button>
      </div>

      <!--
        Responsive grid:
          mobile (default): 1 column
          sm (640+):        2 columns
          lg (1024+):       3 columns
          xl (1280+):       4 columns
        Tighter gap on mobile to maximize card width.
      -->
      <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
        <GameCard v-for="game in filteredGames" :key="game.id" :game="game" />
      </div>
    </section>
  </div>
</template>

<style scoped>
/* Empty state: dimmer wordmark */
.cebolla-wordmark-empty {
  display: block;
  width: clamp(180px, 45%, 300px);
  height: auto;
  opacity: 0.55;
  filter: drop-shadow(0 0 8px rgba(255, 42, 42, 0.20));
}
</style>
