<script setup>
import { ref, computed } from 'vue'
import { useSlate } from '../composables/useSlate.js'
import GameCard from '../components/GameCard.vue'

const { games, loading, error, activeDate } = useSlate()

// Single active filter (null = show all)
const activeFilter = ref(null)

function toggleFilter(key) {
  activeFilter.value = activeFilter.value === key ? null : key
}

const filteredGames = computed(() => {
  if (!activeFilter.value) return games.value
  return games.value.filter(g => {
    const hrf = Number(g.hr_factor_overall) || 0
    const status = (g.status || '').toLowerCase()
    switch (activeFilter.value) {
      case 'hot':   return hrf >= 1.05
      case 'cold':  return hrf > 0 && hrf <= 0.95
      case 'dome':  return g.wind_label === 'dome'
      case 'live':  return status.includes('progress')
      default:      return true
    }
  })
})

// Use the date useSlate decided to load.
// (When no dateStr is passed, useSlate picks the next active slate's date
// automatically. So this is correct even when today's games are all done.)
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
  return new Date().toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
})

const filterChips = [
  { key: 'hot',  label: 'Hot Park',  hint: 'HRF ≥ 1.05' },
  { key: 'cold', label: 'Cold Park', hint: 'HRF ≤ 0.95' },
  { key: 'dome', label: 'Domes',     hint: 'No weather' },
  { key: 'live', label: 'Live',      hint: 'In progress' },
]
</script>

<template>
  <div class="min-h-screen">
    <!-- Compact header with inline filters -->
    <header class="px-6 pt-8 pb-5 border-b border-bg-200">
      <div class="flex items-end justify-between gap-6 flex-wrap mb-4">
        <div>
          <div class="flex items-center gap-3 mb-2">
            <span class="label-bracket text-signal-400">slate · MLB</span>
            <span class="label-caps">{{ dateShort }}</span>
          </div>
          <h1 class="display-text text-3xl text-fg-800 tracking-tight leading-none">
            {{ dateLong }}
          </h1>
        </div>

        <div class="flex items-center gap-5 text-right">
          <div>
            <div class="label-caps">Total</div>
            <div class="display-num text-2xl text-fg-700 leading-none mt-1">{{ games.length }}</div>
          </div>
          <div class="w-px h-8 bg-bg-200"></div>
          <div>
            <div class="label-caps">Showing</div>
            <div class="display-num text-2xl text-fg-700 leading-none mt-1">{{ filteredGames.length }}</div>
          </div>
          <div class="w-px h-8 bg-bg-200"></div>
          <div>
            <div class="label-caps">Updated</div>
            <div class="display-num text-xs text-fg-500 leading-none mt-1">{{ lastUpdate }}</div>
          </div>
        </div>
      </div>

      <!-- Filter chips: radio-style (one at a time) -->
      <div class="flex items-center gap-2 flex-wrap">
        <span class="label-caps mr-2">filter:</span>
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
          <span class="font-mono text-[9px] opacity-70">{{ chip.hint }}</span>
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
    <section class="px-6 py-6">
      <div v-if="loading" class="text-center py-20">
        <div class="inline-flex items-center gap-3 text-fg-500">
          <span class="w-2 h-2 bg-signal-400 animate-pulse"></span>
          <span class="display-text text-lg italic">Peeling layers…</span>
        </div>
      </div>

      <div v-else-if="error" class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">connection error</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>

      <div v-else-if="!games.length" class="text-center py-20">
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

      <!-- 4-column grid (was 5 — gives cards more breathing room) -->
      <div v-else class="grid grid-cols-4 gap-4">
        <GameCard v-for="game in filteredGames" :key="game.id" :game="game" />
      </div>
    </section>
  </div>
</template>
