<script setup>
/**
 * TeamView.vue — Team Deep Dive (M.04)
 *
 * Route: /team/:abbrev
 *
 * Shows everything we know about a team in one screen:
 *   1. Header — team logo + name + abbrev + league/division
 *   2. Recent / upcoming strip — last 5 results + next 5 games (each suppressed if empty)
 *   3. Roster — full active roster, grouped by Pitchers / Batters, with
 *      recent rolling-window stats so you can scan who's hot
 *   4. Team Statcast leaderboards — top barrel%, top HH%, top xSLG (batters only)
 *
 * Data sources:
 *   teams (by abbrev)
 *   players (where team_id = team.id)
 *   games (joined for recent/upcoming, ordered by date)
 *   batter_stats (L14 window for the roster + leaderboards)
 *   pitcher_stats (season window for pitchers)
 */

import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { supabase } from '../supabase.js'
import { teamLogoUrl, hideOnError } from '../utils/mlbImages.js'
import { statColor, fmtStat } from '../utils/percentileColors.js'
import { formatGameTimeShort } from '../utils/timeHelpers.js'
import LoadingBrand from '../components/LoadingBrand.vue'
import FavoriteStar from '../components/FavoriteStar.vue'

const route = useRoute()
const router = useRouter()

const teamAbbrev = computed(() => String(route.params.abbrev || '').toUpperCase())

const team = ref(null)
const roster = ref([])           // all players on team
const recentGames = ref([])      // last 5 finished
const upcomingGames = ref([])    // next 5 scheduled
const batterStats = ref({})      // {player_id: l14 batter_stats row}
const pitcherStats = ref({})     // {player_id: season pitcher_stats row}
const loading = ref(true)
const error = ref(null)

const CURRENT_SEASON = new Date().getFullYear()

// ── Helpers ────────────────────────────────────────────────────
function todayStr() {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

// ── Data load ──────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null

  try {
    // 1. Find the team by abbrev
    const { data: t, error: tErr } = await supabase
      .from('teams')
      .select('id, mlb_id, abbrev, name, league, division, stadium, is_dome, park_hr_factor, park_hr_lhb, park_hr_rhb')
      .eq('abbrev', teamAbbrev.value)
      .maybeSingle()

    if (tErr || !t) {
      throw new Error(tErr?.message || `Team "${teamAbbrev.value}" not found`)
    }
    team.value = t

    // 2. Roster — all players on this team
    const { data: ros } = await supabase
      .from('players')
      .select('id, mlbam_id, name, position, bats, throws, is_pitcher')
      .eq('team_id', t.id)
      .order('is_pitcher', { ascending: true })
      .order('name', { ascending: true })
    roster.value = ros || []

    // 3. Recent + upcoming games in parallel
    const today = todayStr()
    const [recentRes, upcomingRes] = await Promise.all([
      supabase
        .from('games')
        .select(`
          id, mlb_game_pk, game_date, game_time_utc, status,
          home_score, away_score,
          home_team_id, away_team_id,
          away_team:teams!games_away_team_id_fkey ( id, abbrev, name, mlb_id ),
          home_team:teams!games_home_team_id_fkey ( id, abbrev, name, mlb_id )
        `)
        .or(`home_team_id.eq.${t.id},away_team_id.eq.${t.id}`)
        .lt('game_date', today)
        .in('status', ['Final', 'Game Over', 'Completed Early'])
        .order('game_date', { ascending: false })
        .limit(5),
      supabase
        .from('games')
        .select(`
          id, mlb_game_pk, game_date, game_time_utc, status, venue,
          home_team_id, away_team_id,
          away_team:teams!games_away_team_id_fkey ( id, abbrev, name, mlb_id ),
          home_team:teams!games_home_team_id_fkey ( id, abbrev, name, mlb_id )
        `)
        .or(`home_team_id.eq.${t.id},away_team_id.eq.${t.id}`)
        .gte('game_date', today)
        .not('status', 'in', '("Final","Game Over","Completed Early")')
        .order('game_date', { ascending: true })
        .limit(5),
    ])

    recentGames.value = recentRes.data || []
    upcomingGames.value = upcomingRes.data || []

    // 4. Roster stats — batter_stats L14 + pitcher_stats season
    const batterIds = (ros || []).filter(p => !p.is_pitcher).map(p => p.id)
    const pitcherIds = (ros || []).filter(p => p.is_pitcher).map(p => p.id)

    const statPromises = []
    if (batterIds.length) {
      statPromises.push(
        supabase
          .from('batter_stats')
          .select('*')
          .in('batter_id', batterIds)
          .eq('season', CURRENT_SEASON)
          .eq('window_type', 'l14')
          .eq('vs_hand', 'A')
      )
    }
    if (pitcherIds.length) {
      statPromises.push(
        supabase
          .from('pitcher_stats')
          .select('*')
          .in('pitcher_id', pitcherIds)
          .eq('season', CURRENT_SEASON)
          .eq('window_type', 'season')
      )
    }

    const statRes = await Promise.all(statPromises)
    if (batterIds.length) {
      const bMap = {}
      for (const r of (statRes[0]?.data || [])) bMap[r.batter_id] = r
      batterStats.value = bMap
    }
    if (pitcherIds.length) {
      const pIdx = batterIds.length ? 1 : 0
      const pMap = {}
      for (const r of (statRes[pIdx]?.data || [])) pMap[r.pitcher_id] = r
      pitcherStats.value = pMap
    }

    loading.value = false
  } catch (e) {
    console.error('[TeamView] load failed:', e)
    error.value = e.message || String(e)
    loading.value = false
  }
}

onMounted(load)

watch(teamAbbrev, (newAbbrev, oldAbbrev) => {
  if (newAbbrev && newAbbrev !== oldAbbrev) {
    team.value = null
    roster.value = []
    recentGames.value = []
    upcomingGames.value = []
    batterStats.value = {}
    pitcherStats.value = {}
    load()
  }
})

// ── Roster split ───────────────────────────────────────────────
const batters = computed(() => {
  return roster.value
    .filter(p => !p.is_pitcher)
    .map(p => ({
      ...p,
      stats: batterStats.value[p.id] || null,
    }))
})

const pitchers = computed(() => {
  return roster.value
    .filter(p => p.is_pitcher)
    .map(p => ({
      ...p,
      stats: pitcherStats.value[p.id] || null,
    }))
})

// Only show batters with recent activity in the active roster section
const activeBatters = computed(() => batters.value.filter(b => b.stats?.pa))
const inactiveBatters = computed(() => batters.value.filter(b => !b.stats?.pa))
const activePitchers = computed(() => pitchers.value.filter(p => p.stats?.innings_pitched))
const inactivePitchers = computed(() => pitchers.value.filter(p => !p.stats?.innings_pitched))

// ── Game result helpers ────────────────────────────────────────
function gameResult(game) {
  if (!team.value) return null
  const isHome = game.home_team_id === team.value.id
  const teamScore = isHome ? game.home_score : game.away_score
  const oppScore = isHome ? game.away_score : game.home_score
  const opp = isHome ? game.away_team : game.home_team
  if (teamScore == null || oppScore == null) return null
  return {
    isWin: teamScore > oppScore,
    teamScore,
    oppScore,
    opp,
    isHome,
  }
}

function upcomingMatchup(game) {
  if (!team.value) return null
  const isHome = game.home_team_id === team.value.id
  const opp = isHome ? game.away_team : game.home_team
  return { opp, isHome }
}

function fmtGameDate(dateStr) {
  if (!dateStr) return '—'
  const [y, m, d] = dateStr.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

// ── Leaderboards (batters only — pitcher contact is its own page) ──
// Pick top 3 batters per metric, minimum sample so the leaderboard isn't a
// callup with one good BBE topping it.
const MIN_BBE_FOR_LEADERBOARD = 10

function buildLeaderboard(metricKey) {
  return batters.value
    .filter(b => b.stats?.[metricKey] != null && (b.stats?.bbe || 0) >= MIN_BBE_FOR_LEADERBOARD)
    .map(b => ({
      player_id: b.id,
      mlbam_id: b.mlbam_id,
      name: b.name,
      bats: b.bats,
      position: b.position,
      value: Number(b.stats[metricKey]),
    }))
    .sort((a, b) => b.value - a.value)
    .slice(0, 3)
}

const leaderboardBrl = computed(() => buildLeaderboard('barrel_pct'))
const leaderboardHH  = computed(() => buildLeaderboard('hard_hit_pct'))
const leaderboardXSLG = computed(() => buildLeaderboard('xslg'))

const hasAnyLeaderboard = computed(() =>
  leaderboardBrl.value.length || leaderboardHH.value.length || leaderboardXSLG.value.length
)

// ── Misc display helpers ───────────────────────────────────────
const teamLogo = computed(() => teamLogoUrl(team.value?.mlb_id))

function fmtRate2(v) {
  return v != null ? Number(v).toFixed(2) : '—'
}
function fmtPct1(v) {
  return v != null ? `${Number(v).toFixed(1)}%` : '—'
}
function fmtAvg(v) {
  if (v == null) return '—'
  return v < 1
    ? `.${Math.round(v * 1000).toString().padStart(3, '0')}`
    : v.toFixed(3)
}
</script>

<template>
  <div class="min-h-screen">
    <div class="max-w-5xl mx-auto">

      <!-- ← back -->
      <div class="px-4 sm:px-6 pt-4 sm:pt-5 pb-2 flex items-center gap-4">
        <button
          @click="router.back()"
          class="label-caps hover:text-signal-400 transition inline-flex items-center gap-2"
        >
          <span>←</span><span>back</span>
        </button>
        <span class="label-bracket !text-[9px] opacity-60">M.04 · TEAM</span>
      </div>

      <LoadingBrand v-if="loading" text="Loading team…" />

      <div v-else-if="error" class="px-6 py-10">
        <div class="border border-signal-400/30 bg-signal-400/5 p-5">
          <div class="label-bracket text-signal-400 mb-2">team not found</div>
          <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
        </div>
      </div>

      <template v-else-if="team">
        <!-- ── HEADER ───────────────────────────────────────── -->
        <header class="px-4 sm:px-6 pb-4 border-b border-bg-200">
          <div class="flex items-center gap-3 sm:gap-4">
            <img
              v-if="teamLogo"
              :src="teamLogo"
              :alt="team.abbrev"
              class="team-logo-xl"
              @error="hideOnError"
            />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-1.5">
                <h1 class="display-text text-2xl sm:text-3xl text-fg-800 tracking-tight leading-none truncate">
                  {{ team.name }}
                </h1>
                <FavoriteStar kind="team" size="lg" :item="team" />
              </div>
              <div class="flex items-baseline gap-2 sm:gap-3 flex-wrap">
                <span class="label-bracket text-signal-400">{{ team.abbrev }}</span>
                <span v-if="team.league" class="label-caps">{{ team.league }}</span>
                <span v-if="team.division" class="label-caps">{{ team.division }}</span>
                <span v-if="team.is_dome" class="font-mono text-[10px] text-fg-500">· dome</span>
              </div>
            </div>
          </div>
        </header>

        <!-- ── RECENT + UPCOMING STRIP ──────────────────────── -->
        <section
          v-if="recentGames.length || upcomingGames.length"
          class="px-4 sm:px-6 py-4 border-b border-bg-200"
        >
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <!-- Recent -->
            <div v-if="recentGames.length">
              <div class="flex items-baseline justify-between mb-2">
                <h2 class="label-bracket text-signal-400">recent · last {{ recentGames.length }}</h2>
              </div>
              <div class="space-y-1.5">
                <router-link
                  v-for="g in recentGames"
                  :key="`r-${g.id}`"
                  :to="{ name: 'hr-report', params: { gameId: g.id } }"
                  class="game-strip"
                >
                  <span class="label-caps !text-[9px] text-fg-500 w-12 shrink-0">
                    {{ fmtGameDate(g.game_date) }}
                  </span>
                  <span
                    v-if="gameResult(g)"
                    class="result-badge"
                    :class="gameResult(g).isWin ? 'is-win' : 'is-loss'"
                  >
                    {{ gameResult(g).isWin ? 'W' : 'L' }}
                  </span>
                  <span class="display-num text-xs text-fg-700">
                    {{ gameResult(g)?.teamScore ?? '—' }}–{{ gameResult(g)?.oppScore ?? '—' }}
                  </span>
                  <span class="text-fg-500 text-xs">
                    {{ gameResult(g)?.isHome ? 'vs' : '@' }}
                  </span>
                  <span class="label-bracket text-fg-600">{{ gameResult(g)?.opp?.abbrev || '—' }}</span>
                </router-link>
              </div>
            </div>

            <!-- Upcoming -->
            <div v-if="upcomingGames.length">
              <div class="flex items-baseline justify-between mb-2">
                <h2 class="label-bracket text-signal-400">upcoming · next {{ upcomingGames.length }}</h2>
              </div>
              <div class="space-y-1.5">
                <router-link
                  v-for="g in upcomingGames"
                  :key="`u-${g.id}`"
                  :to="{ name: 'hr-report', params: { gameId: g.id } }"
                  class="game-strip"
                >
                  <span class="label-caps !text-[9px] text-fg-500 w-12 shrink-0">
                    {{ fmtGameDate(g.game_date) }}
                  </span>
                  <span class="display-num !text-[10px] text-fg-500 w-12 shrink-0">
                    {{ formatGameTimeShort(g.game_time_utc) }}
                  </span>
                  <span class="text-fg-500 text-xs">
                    {{ upcomingMatchup(g)?.isHome ? 'vs' : '@' }}
                  </span>
                  <span class="label-bracket text-fg-600">{{ upcomingMatchup(g)?.opp?.abbrev || '—' }}</span>
                </router-link>
              </div>
            </div>
          </div>
        </section>

        <!-- ── STATCAST LEADERBOARDS ─────────────────────────── -->
        <section v-if="hasAnyLeaderboard" class="px-4 sm:px-6 py-4 border-b border-bg-200">
          <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
            <h2 class="label-bracket text-signal-400">statcast leaders · L14</h2>
            <span class="label-caps !text-[8px] opacity-70">
              top 3 · min {{ MIN_BBE_FOR_LEADERBOARD }} BBE
            </span>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <!-- Barrel% -->
            <div class="leaderboard-card">
              <div class="leaderboard-head">
                <span class="label-bracket text-signal-400">Brl%</span>
              </div>
              <div v-if="leaderboardBrl.length" class="leaderboard-rows">
                <router-link
                  v-for="(r, i) in leaderboardBrl"
                  :key="`brl-${r.player_id}`"
                  :to="{ name: 'player', params: { playerId: r.player_id } }"
                  class="leaderboard-row"
                >
                  <span class="leaderboard-rank">{{ i + 1 }}</span>
                  <span class="leaderboard-name">{{ r.name }}</span>
                  <span class="display-num text-[11px]" :class="statColor(r.value, 'barrel_pct', 'batter')">
                    {{ fmtPct1(r.value) }}
                  </span>
                </router-link>
              </div>
              <div v-else class="leaderboard-empty">No qualified batters yet</div>
            </div>

            <!-- HH% -->
            <div class="leaderboard-card">
              <div class="leaderboard-head">
                <span class="label-bracket text-signal-400">HH%</span>
              </div>
              <div v-if="leaderboardHH.length" class="leaderboard-rows">
                <router-link
                  v-for="(r, i) in leaderboardHH"
                  :key="`hh-${r.player_id}`"
                  :to="{ name: 'player', params: { playerId: r.player_id } }"
                  class="leaderboard-row"
                >
                  <span class="leaderboard-rank">{{ i + 1 }}</span>
                  <span class="leaderboard-name">{{ r.name }}</span>
                  <span class="display-num text-[11px]" :class="statColor(r.value, 'hard_hit_pct', 'batter')">
                    {{ fmtPct1(r.value) }}
                  </span>
                </router-link>
              </div>
              <div v-else class="leaderboard-empty">No qualified batters yet</div>
            </div>

            <!-- xSLG -->
            <div class="leaderboard-card">
              <div class="leaderboard-head">
                <span class="label-bracket text-signal-400">xSLG</span>
              </div>
              <div v-if="leaderboardXSLG.length" class="leaderboard-rows">
                <router-link
                  v-for="(r, i) in leaderboardXSLG"
                  :key="`xslg-${r.player_id}`"
                  :to="{ name: 'player', params: { playerId: r.player_id } }"
                  class="leaderboard-row"
                >
                  <span class="leaderboard-rank">{{ i + 1 }}</span>
                  <span class="leaderboard-name">{{ r.name }}</span>
                  <span class="display-num text-[11px]" :class="statColor(r.value, 'xslg', 'batter')">
                    {{ fmtAvg(r.value) }}
                  </span>
                </router-link>
              </div>
              <div v-else class="leaderboard-empty">No qualified batters yet</div>
            </div>
          </div>
        </section>

        <!-- ── ROSTER · PITCHERS ─────────────────────────────── -->
        <section v-if="pitchers.length" class="px-4 sm:px-6 py-4 border-b border-bg-200">
          <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
            <h2 class="label-bracket text-signal-400">pitchers</h2>
            <span class="label-caps !text-[8px] opacity-70">season stats</span>
          </div>

          <!-- Active pitchers table -->
          <div v-if="activePitchers.length" class="bg-bg-50 border border-bg-200 overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left">
                  <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Name</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-center w-10">Thr</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">IP</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">ERA</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">WHIP</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">K/9</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">HR/9</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="p in activePitchers"
                  :key="p.id"
                  class="group hover:bg-bg-100/50 transition-colors"
                >
                  <td class="py-2 px-3 border-b border-bg-200/40">
                    <router-link
                      :to="{ name: 'player', params: { playerId: p.id } }"
                      class="text-fg-700 text-sm group-hover:text-signal-200 transition"
                    >
                      {{ p.name }}
                    </router-link>
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-center font-mono text-[10px] text-fg-500">
                    {{ p.throws || '?' }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ p.stats?.innings_pitched != null ? Number(p.stats.innings_pitched).toFixed(1) : '—' }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ fmtRate2(p.stats?.era) }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ fmtRate2(p.stats?.whip) }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ fmtRate2(p.stats?.k_per_9) }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ fmtRate2(p.stats?.hr_per_9) }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Inactive pitchers (no IP yet) -->
          <details v-if="inactivePitchers.length" class="mt-3">
            <summary class="label-caps !text-[9px] text-fg-500 cursor-pointer hover:text-fg-700 transition">
              + {{ inactivePitchers.length }} pitchers with no recorded innings
            </summary>
            <div class="flex flex-wrap gap-x-3 gap-y-1 mt-2 pl-3 border-l border-bg-300">
              <router-link
                v-for="p in inactivePitchers"
                :key="p.id"
                :to="{ name: 'player', params: { playerId: p.id } }"
                class="text-fg-500 text-xs hover:text-signal-200 transition"
              >
                {{ p.name }} <span class="text-fg-600 font-mono">·{{ p.throws || '?' }}</span>
              </router-link>
            </div>
          </details>
        </section>

        <!-- ── ROSTER · BATTERS ──────────────────────────────── -->
        <section v-if="batters.length" class="px-4 sm:px-6 py-4 pb-8">
          <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
            <h2 class="label-bracket text-signal-400">batters</h2>
            <span class="label-caps !text-[8px] opacity-70">L14 statcast</span>
          </div>

          <!-- Active batters table -->
          <div v-if="activeBatters.length" class="bg-bg-50 border border-bg-200 overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left">
                  <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Name</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-center w-10">Bats</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">PA</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">HR</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Brl%</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">HH%</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">xBA</th>
                  <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">xSLG</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="b in activeBatters"
                  :key="b.id"
                  class="group hover:bg-bg-100/50 transition-colors"
                >
                  <td class="py-2 px-3 border-b border-bg-200/40">
                    <router-link
                      :to="{ name: 'player', params: { playerId: b.id } }"
                      class="text-fg-700 text-sm group-hover:text-signal-200 transition inline-flex items-center gap-1.5"
                    >
                      {{ b.name }}
                      <span v-if="b.position" class="font-mono text-[9px] text-fg-500">·{{ b.position }}</span>
                    </router-link>
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-center font-mono text-[10px] text-fg-500">
                    {{ b.bats || '?' }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ b.stats?.pa ?? '—' }}
                  </td>
                  <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-700">
                    {{ b.stats?.hr ?? '—' }}
                  </td>
                  <td
                    class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs"
                    :class="statColor(b.stats?.barrel_pct, 'barrel_pct', 'batter')"
                  >
                    {{ b.stats?.barrel_pct != null ? fmtStat(b.stats.barrel_pct, 'barrel_pct') : '—' }}
                  </td>
                  <td
                    class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs"
                    :class="statColor(b.stats?.hard_hit_pct, 'hard_hit_pct', 'batter')"
                  >
                    {{ b.stats?.hard_hit_pct != null ? fmtStat(b.stats.hard_hit_pct, 'hard_hit_pct') : '—' }}
                  </td>
                  <td
                    class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs"
                    :class="statColor(b.stats?.xba, 'xba', 'batter')"
                  >
                    {{ b.stats?.xba != null ? fmtStat(b.stats.xba, 'xba') : '—' }}
                  </td>
                  <td
                    class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs"
                    :class="statColor(b.stats?.xslg, 'xslg', 'batter')"
                  >
                    {{ b.stats?.xslg != null ? fmtStat(b.stats.xslg, 'xslg') : '—' }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Inactive batters (no PA in L14) -->
          <details v-if="inactiveBatters.length" class="mt-3">
            <summary class="label-caps !text-[9px] text-fg-500 cursor-pointer hover:text-fg-700 transition">
              + {{ inactiveBatters.length }} batters with no recent at-bats
            </summary>
            <div class="flex flex-wrap gap-x-3 gap-y-1 mt-2 pl-3 border-l border-bg-300">
              <router-link
                v-for="b in inactiveBatters"
                :key="b.id"
                :to="{ name: 'player', params: { playerId: b.id } }"
                class="text-fg-500 text-xs hover:text-signal-200 transition"
              >
                {{ b.name }} <span v-if="b.position" class="text-fg-600 font-mono">·{{ b.position }}</span>
              </router-link>
            </div>
          </details>
        </section>
      </template>

      <!-- Truly nothing found (shouldn't happen but covers route mistakes) -->
      <div v-else class="px-6 py-20 text-center">
        <div class="display-text text-2xl text-fg-500 italic mb-2">Team not found</div>
        <p class="text-fg-500 text-xs">
          No team with abbrev <span class="display-num">{{ teamAbbrev }}</span> in the database.
        </p>
        <router-link to="/" class="inline-block mt-6 label-caps hover:text-signal-400 transition">
          ← back to slate
        </router-link>
      </div>

    </div><!-- /max-w-5xl -->
  </div>
</template>

<style scoped>
.team-logo-xl {
  width: 56px;
  height: 56px;
  object-fit: contain;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  filter: grayscale(0.10) brightness(1.05);
  border: 1px solid rgba(255, 255, 255, 0.06);
}
@media (min-width: 640px) {
  .team-logo-xl {
    width: 72px;
    height: 72px;
  }
}

/* Recent/upcoming game strip — single-line links */
.game-strip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 8px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid transparent;
  transition: border-color 120ms ease, background-color 120ms ease;
}
.game-strip:hover {
  border-color: rgba(255, 42, 42, 0.30);
  background: rgba(255, 42, 42, 0.05);
}

.result-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  font-weight: 600;
  line-height: 1;
}
.result-badge.is-win {
  color: rgba(255, 42, 42, 1);
  background: rgba(255, 42, 42, 0.12);
  border: 1px solid rgba(255, 42, 42, 0.45);
}
.result-badge.is-loss {
  color: rgba(95, 165, 255, 0.85);
  background: rgba(95, 165, 255, 0.08);
  border: 1px solid rgba(95, 165, 255, 0.30);
}

/* Leaderboard cards */
.leaderboard-card {
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.08);
}
.leaderboard-head {
  padding: 6px 10px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.leaderboard-rows {
  padding: 4px 0;
}
.leaderboard-row {
  display: grid;
  grid-template-columns: 18px 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  color: rgba(255, 255, 255, 0.78);
  text-decoration: none;
  transition: background-color 100ms ease, color 100ms ease;
}
.leaderboard-row:hover {
  background: rgba(255, 42, 42, 0.06);
  color: #fff;
}
.leaderboard-rank {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: rgba(255, 255, 255, 0.40);
  text-align: center;
}
.leaderboard-name {
  font-size: 12px;
  font-weight: 500;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.leaderboard-empty {
  padding: 14px 10px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.40);
  font-style: italic;
  text-align: center;
}
</style>
