<script setup>
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useGame } from '../composables/useGame.js'
import ArsenalGrid from '../components/ArsenalGrid.vue'
import BatterTable from '../components/BatterTable.vue'

const route = useRoute()
const gameId = Number(route.params.gameId)

const {
  game, awayLineup, homeLineup,
  arsenalAway, arsenalHome,
  batterStats, odds, bvp,
  loading, error,
} = useGame(gameId)

const showSecondary = ref(false)
const secondaryMarket = ref('hits')   // 'hits' | 'rbi'

const gameTime = computed(() => {
  if (!game.value?.game_time_utc) return ''
  return new Date(game.value.game_time_utc).toLocaleTimeString('en-US', {
    hour: 'numeric', minute: '2-digit',
  })
})

const statusBadge = computed(() => {
  const s = (game.value?.status || '').toLowerCase()
  if (s.includes('progress')) return { text: 'LIVE', color: 'text-signal-400 bg-signal-400/15' }
  if (s.includes('final') || s.includes('over')) return { text: 'FINAL', color: 'text-fg-500 bg-bg-200' }
  if (s.includes('pre')) return { text: 'SOON', color: 'text-fg-600 bg-bg-200/60' }
  return null
})

function hrfTone(v) {
  if (v == null) return 'text-fg-400'
  const n = Number(v)
  if (n >= 1.10) return 'text-signal-400'
  if (n >= 1.03) return 'text-signal-200'
  if (n <= 0.92) return 'text-edge-cold-1'
  if (n <= 0.97) return 'text-edge-cold-2'
  return 'text-fg-600'
}

function fmtFactor(v) {
  return v != null ? Number(v).toFixed(2) : '—'
}
</script>

<template>
  <div class="min-h-screen">
    <!-- Top breadcrumb -->
    <div class="px-6 pt-5 pb-2">
      <router-link to="/" class="label-caps hover:text-signal-400 transition inline-flex items-center gap-2">
        <span>←</span><span>slate</span>
      </router-link>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="text-center py-32">
      <div class="inline-flex items-center gap-3 text-fg-500">
        <span class="w-2 h-2 bg-signal-400 animate-pulse"></span>
        <span class="display-text text-lg italic">Loading matchup…</span>
      </div>
    </div>

    <!-- Error -->
    <div v-else-if="error" class="px-6 pb-8">
      <div class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">error</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>
    </div>

    <template v-else-if="game">
      <!-- Header strip -->
      <header class="px-6 pb-5 border-b border-bg-200">
        <div class="flex items-baseline justify-between gap-6 flex-wrap mb-4">
          <div>
            <div class="flex items-center gap-3 mb-2">
              <span class="label-bracket text-signal-400">M.01.b · HR REPORT</span>
              <span v-if="statusBadge"
                    class="text-[9px] uppercase tracking-wide2 font-mono px-2 py-0.5 rounded-sm"
                    :class="statusBadge.color">
                {{ statusBadge.text }}
              </span>
            </div>
            <div class="flex items-baseline gap-4">
              <h1 class="display-text text-4xl text-fg-800 tracking-tight leading-none">
                {{ game.away_team?.abbrev }}
                <span class="text-fg-400 italic mx-1">@</span>
                {{ game.home_team?.abbrev }}
              </h1>
              <span class="display-num text-sm text-fg-500">{{ gameTime }}</span>
              <span class="label-caps">{{ game.venue }}</span>
            </div>
          </div>
        </div>

        <!-- Conditions row -->
        <div class="grid grid-cols-2 md:grid-cols-6 gap-x-6 gap-y-3">
          <div>
            <div class="label-caps">Temp</div>
            <div class="display-num text-lg text-fg-700 mt-0.5">
              {{ game.temp_f != null ? `${Math.round(game.temp_f)}°` : '—' }}
            </div>
          </div>
          <div>
            <div class="label-caps">Wind</div>
            <div class="display-num text-sm mt-1 text-fg-600">
              {{ game.wind_label === 'dome' ? 'dome' : (game.wind_label ? `${Math.round(game.wind_mph || 0)}mph ${game.wind_label}` : '—') }}
            </div>
          </div>
          <div>
            <div class="label-caps">Rain</div>
            <div class="display-num text-lg text-fg-700 mt-0.5">
              {{ game.precip_pct != null ? `${game.precip_pct}%` : '—' }}
            </div>
          </div>
          <div>
            <div class="label-caps">HR Factor</div>
            <div class="display-num text-lg mt-0.5" :class="hrfTone(game.hr_factor_overall)">
              {{ fmtFactor(game.hr_factor_overall) }}
            </div>
          </div>
          <div>
            <div class="label-caps">LHB Factor</div>
            <div class="display-num text-lg mt-0.5" :class="hrfTone(game.hr_factor_lhb)">
              {{ fmtFactor(game.hr_factor_lhb) }}
            </div>
          </div>
          <div>
            <div class="label-caps">RHB Factor</div>
            <div class="display-num text-lg mt-0.5" :class="hrfTone(game.hr_factor_rhb)">
              {{ fmtFactor(game.hr_factor_rhb) }}
            </div>
          </div>
        </div>
      </header>

      <!-- Pitcher arsenals: side by side -->
      <section class="px-6 py-5">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="label-bracket text-signal-400">probable starters</h2>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <ArsenalGrid
            :pitcher="game.away_pitcher"
            :arsenal="arsenalAway"
            :label="`${game.away_team?.abbrev} · AWAY`"
          />
          <ArsenalGrid
            :pitcher="game.home_pitcher"
            :arsenal="arsenalHome"
            :label="`${game.home_team?.abbrev} · HOME`"
          />
        </div>
      </section>

      <!-- HR matchup tables: side by side -->
      <section class="px-6 pb-5">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="label-bracket text-signal-400">HR matchups</h2>
          <span class="label-caps">Anytime Home Run · DraftKings</span>
        </div>
        <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <BatterTable
            :lineup="awayLineup"
            :batter-stats="batterStats"
            :odds="odds"
            :bvp="bvp"
            :pitcher-id="game.home_pitcher?.id"
            :team-label="`${game.away_team?.abbrev} BATTERS`"
            market-mode="hr"
          />
          <BatterTable
            :lineup="homeLineup"
            :batter-stats="batterStats"
            :odds="odds"
            :bvp="bvp"
            :pitcher-id="game.away_pitcher?.id"
            :team-label="`${game.home_team?.abbrev} BATTERS`"
            market-mode="hr"
          />
        </div>
      </section>

      <!-- Collapsible Hits/RBI section -->
      <section class="px-6 pb-10">
        <button
          @click="showSecondary = !showSecondary"
          class="w-full py-3 border border-bg-200 hover:border-signal-400/40 transition flex items-center justify-center gap-3 group"
        >
          <span class="label-caps group-hover:text-signal-400 transition">
            {{ showSecondary ? '− hide' : '+ show' }} hits / RBI markets
          </span>
        </button>

        <div v-if="showSecondary" class="mt-5">
          <!-- Market toggle -->
          <div class="flex items-center gap-2 mb-3">
            <span class="label-caps mr-2">market:</span>
            <button
              v-for="m in ['hits', 'rbi']"
              :key="m"
              @click="secondaryMarket = m"
              class="text-[11px] px-2.5 py-1 border transition"
              :class="secondaryMarket === m
                ? 'border-signal-400 text-signal-200 bg-signal-400/10'
                : 'border-bg-200 text-fg-500 hover:border-bg-300 hover:text-fg-700'"
            >
              {{ m.toUpperCase() }}
            </button>
          </div>

          <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <BatterTable
              :lineup="awayLineup"
              :batter-stats="batterStats"
              :odds="odds"
              :bvp="bvp"
              :pitcher-id="game.home_pitcher?.id"
              :team-label="`${game.away_team?.abbrev} BATTERS`"
              :market-mode="secondaryMarket"
            />
            <BatterTable
              :lineup="homeLineup"
              :batter-stats="batterStats"
              :odds="odds"
              :bvp="bvp"
              :pitcher-id="game.away_pitcher?.id"
              :team-label="`${game.home_team?.abbrev} BATTERS`"
              :market-mode="secondaryMarket"
            />
          </div>
        </div>
      </section>

      <!-- Footer note -->
      <footer class="px-6 pb-8 text-center">
        <p class="label-caps !text-[9px] opacity-50">
          Edge values pending model · Phase 4
        </p>
      </footer>
    </template>
  </div>
</template>
