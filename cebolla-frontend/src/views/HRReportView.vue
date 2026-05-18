<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useGame } from '../composables/useGame.js'
import ArsenalGrid from '../components/ArsenalGrid.vue'
import BatterTable from '../components/BatterTable.vue'
import LogBetModal from '../components/LogBetModal.vue'
import PitcherAllowedStats from '../components/PitcherAllowedStats.vue'
import InfoTooltip from '../components/InfoTooltip.vue'
import { formatGameTime, formatCountdown, minutesUntil } from '../utils/timeHelpers.js'

// Tick to refresh countdowns every 30s
const tickKey = ref(0)
let tickTimer = null
onMounted(() => { tickTimer = setInterval(() => tickKey.value++, 30_000) })
onUnmounted(() => { if (tickTimer) clearInterval(tickTimer) })

const route = useRoute()
const gameId = Number(route.params.gameId)

const {
  game, awayLineup, homeLineup,
  arsenalAway, arsenalHome,
  batterStats, odds, bvp, projections,
  loading, error,
} = useGame(gameId)

const showSecondary = ref(false)
const secondaryMarket = ref('hits')

// ─── Bet logging state ───
const showLogModal = ref(false)
const logBetCtx = ref({ player: {}, proj: {}, marketMode: 'hr' })
function onLogBet(payload) {
  logBetCtx.value = payload
  showLogModal.value = true
}
function onBetLogged() {
  // Could surface a toast here; for now just close
  showLogModal.value = false
}

const gameTime = computed(() => {
  return formatGameTime(game.value?.game_time_utc)
})

const gameCountdown = computed(() => {
  const mins = minutesUntil(game.value?.game_time_utc)
  if (mins == null) return null
  if (mins <= 0 || mins > 240) return null
  return formatCountdown(game.value?.game_time_utc)
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

// Model meta — which model version are we showing
const modelMeta = computed(() => {
  const proj = Object.values(projections.value)[0]
  return proj?.model_version || null
})
</script>

<template>
  <div class="min-h-screen">
    <div class="px-4 sm:px-6 pt-4 sm:pt-5 pb-2">
      <router-link to="/" class="label-caps hover:text-signal-400 transition inline-flex items-center gap-2">
        <span>←</span><span>slate</span>
      </router-link>
    </div>

    <div v-if="loading" class="text-center py-32">
      <div class="inline-flex items-center gap-3 text-fg-500">
        <span class="w-2 h-2 bg-signal-400 animate-pulse"></span>
        <span class="display-text text-lg italic">Loading matchup…</span>
      </div>
    </div>

    <div v-else-if="error" class="px-6 pb-8">
      <div class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">error</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>
    </div>

    <template v-else-if="game">
      <header class="px-4 sm:px-6 pb-5 border-b border-bg-200">
        <div class="flex items-baseline justify-between gap-3 sm:gap-6 flex-wrap mb-4">
          <div class="min-w-0">
            <div class="flex items-center gap-2 sm:gap-3 mb-2 flex-wrap">
              <span class="label-bracket text-signal-400">M.01.b · HR REPORT</span>
              <span v-if="statusBadge"
                    class="text-[9px] uppercase tracking-wide2 font-mono px-2 py-0.5 rounded-sm"
                    :class="statusBadge.color">
                {{ statusBadge.text }}
              </span>
              <span v-if="modelMeta"
                    class="label-bracket !text-[9px] opacity-60">model {{ modelMeta }}</span>
            </div>
            <!-- Teams + time/venue: row on desktop, wraps tighter on mobile -->
            <div class="flex items-baseline gap-2 sm:gap-4 flex-wrap">
              <h1 class="display-text text-3xl sm:text-4xl text-fg-800 tracking-tight leading-none">
                {{ game.away_team?.abbrev }}
                <span class="text-fg-400 italic mx-1">@</span>
                {{ game.home_team?.abbrev }}
              </h1>
              <span class="display-num text-sm text-fg-500">{{ gameTime }}</span>
              <span v-if="gameCountdown" class="display-num text-xs text-signal-200">
                ({{ gameCountdown }})
              </span>
              <span class="label-caps">{{ game.venue }}</span>
            </div>
          </div>
        </div>

        <!-- Stats grid: 3-col mobile / 6-col desktop (was 2-col / 6-col, now denser on mobile) -->
        <div class="grid grid-cols-3 md:grid-cols-6 gap-x-3 sm:gap-x-6 gap-y-3">
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
            <div class="label-caps inline-flex items-center">
              HR Factor <InfoTooltip term="hr_factor" />
            </div>
            <div class="display-num text-lg mt-0.5" :class="hrfTone(game.hr_factor_overall)">
              {{ fmtFactor(game.hr_factor_overall) }}
            </div>
          </div>
          <div>
            <div class="label-caps inline-flex items-center">
              LHB Factor <InfoTooltip term="hr_factor_lhb" />
            </div>
            <div class="display-num text-lg mt-0.5" :class="hrfTone(game.hr_factor_lhb)">
              {{ fmtFactor(game.hr_factor_lhb) }}
            </div>
          </div>
          <div>
            <div class="label-caps inline-flex items-center">
              RHB Factor <InfoTooltip term="hr_factor_rhb" />
            </div>
            <div class="display-num text-lg mt-0.5" :class="hrfTone(game.hr_factor_rhb)">
              {{ fmtFactor(game.hr_factor_rhb) }}
            </div>
          </div>
        </div>
      </header>

      <section class="px-4 sm:px-6 py-5">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="label-bracket text-signal-400">probable starters</h2>
        </div>
        <!-- Stacks vertically on mobile/tablet, side-by-side at lg+ -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3 sm:gap-4">
          <div class="bg-bg-50 border border-bg-200">
            <ArsenalGrid
              :pitcher="game.away_pitcher"
              :arsenal="arsenalAway"
              :label="`${game.away_team?.abbrev} · AWAY`"
            />
            <PitcherAllowedStats :pitcher-id="game.away_pitcher?.id" />
          </div>
          <div class="bg-bg-50 border border-bg-200">
            <ArsenalGrid
              :pitcher="game.home_pitcher"
              :arsenal="arsenalHome"
              :label="`${game.home_team?.abbrev} · HOME`"
            />
            <PitcherAllowedStats :pitcher-id="game.home_pitcher?.id" />
          </div>
        </div>
      </section>

      <section class="px-4 sm:px-6 pb-5">
        <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
          <h2 class="label-bracket text-signal-400">HR matchups</h2>
          <span class="label-caps text-right">Anytime Home Run · DraftKings</span>
        </div>
        <div class="grid grid-cols-1 xl:grid-cols-2 gap-3 sm:gap-4">
          <BatterTable
            :lineup="awayLineup"
            :batter-stats="batterStats"
            :odds="odds"
            :bvp="bvp"
            :projections="projections"
            :pitcher-id="game.home_pitcher?.id"
            :team-label="`${game.away_team?.abbrev} BATTERS`"
            market-mode="hr"
            :game-id="gameId"
            :game-time-utc="game.game_time_utc"
            @log-bet="onLogBet"
          />
          <BatterTable
            :lineup="homeLineup"
            :batter-stats="batterStats"
            :odds="odds"
            :bvp="bvp"
            :projections="projections"
            :pitcher-id="game.away_pitcher?.id"
            :team-label="`${game.home_team?.abbrev} BATTERS`"
            market-mode="hr"
            :game-id="gameId"
            :game-time-utc="game.game_time_utc"
            @log-bet="onLogBet"
          />
        </div>
      </section>

      <section class="px-4 sm:px-6 pb-10">
        <button
          @click="showSecondary = !showSecondary"
          class="w-full py-3 border border-bg-200 hover:border-signal-400/40 transition flex items-center justify-center gap-3 group"
        >
          <span class="label-caps group-hover:text-signal-400 transition">
            {{ showSecondary ? '− hide' : '+ show' }} hits / RBI markets
          </span>
        </button>

        <div v-if="showSecondary" class="mt-5">
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

          <div class="grid grid-cols-1 xl:grid-cols-2 gap-3 sm:gap-4">
            <BatterTable
              :lineup="awayLineup"
              :batter-stats="batterStats"
              :odds="odds"
              :bvp="bvp"
              :projections="projections"
              :pitcher-id="game.home_pitcher?.id"
              :team-label="`${game.away_team?.abbrev} BATTERS`"
              :market-mode="secondaryMarket"
              :game-id="gameId"
              :game-time-utc="game.game_time_utc"
              @log-bet="onLogBet"
            />
            <BatterTable
              :lineup="homeLineup"
              :batter-stats="batterStats"
              :odds="odds"
              :bvp="bvp"
              :projections="projections"
              :pitcher-id="game.away_pitcher?.id"
              :team-label="`${game.home_team?.abbrev} BATTERS`"
              :market-mode="secondaryMarket"
              :game-id="gameId"
              :game-time-utc="game.game_time_utc"
              @log-bet="onLogBet"
            />
          </div>
        </div>
      </section>

      <footer class="px-6 pb-8 text-center">
        <p class="label-caps !text-[9px] opacity-50">
          Edges are model estimates. Hits / RBI projections coming next.
        </p>
      </footer>
    </template>

    <!-- Bet logging modal -->
    <LogBetModal
      :open="showLogModal"
      :game-id="gameId"
      :player="logBetCtx.player"
      :proj="logBetCtx.proj"
      :market-mode="logBetCtx.marketMode"
      @close="showLogModal = false"
      @logged="onBetLogged"
    />
  </div>
</template>
