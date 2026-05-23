<script setup>
/**
 * PlayerView.vue — Player Deep Dive page (M.01.c)
 *
 * Shows everything we know about a batter in one screen, intended for sharps
 * studying a specific player before placing a bet:
 *
 *   1. Header — headshot + name + team + bats/position
 *   2. Tonight's matchup card — if the player is in any upcoming lineup
 *   3. Season quick stats card — PA/HR/BA/SLG anchor
 *   4. Statcast trajectory — Brl%, HH%, xSLG, xBA across Season → L30 → L14 → L7
 *      with trend arrows so the form story is immediately readable
 *   5. Vs LHP / vs RHP splits (season only)
 *   6. Pitch-type breakdown (season) — how this hitter performs vs each pitch
 *
 * Data sources:
 *   players (by id)
 *   batter_stats (4 windows: season, l30, l14, l7) with vs_hand A/L/R for season
 *   lineups (for tonight's matchup detection)
 *   games (joined for tonight's pitcher info)
 *   pitcher_stats (for tonight's pitcher's allowed stats, L14)
 */

import { ref, computed, onMounted, watch, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { supabase } from '../supabase.js'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'
import { statColor, fmtStat } from '../utils/percentileColors.js'
import { formatGameTime, formatCountdown } from '../utils/timeHelpers.js'
import { useTodaysPOD } from '../composables/useTodaysPOD.js'
import InfoTooltip from '../components/InfoTooltip.vue'
import LoadingBrand from '../components/LoadingBrand.vue'

/**
 * Hi-res MLB headshot URL builder. We override mlbImages.js's default
 * size (likely 213w) to request 480w — looks sharp on 96px display @ 2x DPI.
 */
function hiResHeadshotUrl(mlbamId) {
  if (!mlbamId) return ''
  return `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_480,q_auto:best/v1/people/${mlbamId}/headshot/67/current`
}

const route = useRoute()
const router = useRouter()

const playerId = computed(() => parseInt(route.params.playerId))

// Today's POD lookup (shared module-level cache)
const { isPOD } = useTodaysPOD()

const player = ref(null)
const team = ref(null)
const windows = ref([])          // [{window_type, ...stats}]
const splitVsL = ref(null)
const splitVsR = ref(null)
const tonightGame = ref(null)    // {game, pitcher, pitcher_team, lineup_row}
const pitcherAllowed = ref(null) // L14 pitcher_stats for tonight's pitcher
const trendData = ref(null)      // latest batter_trends row (Combined Heat + per-metric)
const loading = ref(true)
const error = ref(null)

const CURRENT_SEASON = new Date().getFullYear()

const WINDOW_ORDER = ['season', 'l30', 'l14', 'l7']
const WINDOW_LABEL = {
  season: 'Season',
  l30:    'L30',
  l14:    'L14',
  l7:     'L7',
}

// ── Data load ──────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null

  try {
    // 1. Player record + team (must succeed before anything else — gives us
    //    the existence check and the team data).
    const { data: p, error: pErr } = await supabase
      .from('players')
      .select(`
        id, mlbam_id, name, position, bats, throws, team_id,
        team:teams ( id, abbrev, name, stadium )
      `)
      .eq('id', playerId.value)
      .single()

    if (pErr || !p) throw new Error(pErr?.message || 'Player not found')

    player.value = p
    team.value = p.team || null

    // 2. Everything else in parallel — no inter-dependency.
    //    Previous version did these sequentially: ~6 round-trips of waterfall.
    //    On mobile (~200ms RTT) that's ~1.2 seconds saved by parallelizing.
    const [windowsRes, splitsRes, trendsRes] = await Promise.all([
      // Windows (season, l30, l14, l7) for overall vs_hand='A'
      supabase
        .from('batter_stats')
        .select('*')
        .eq('batter_id', playerId.value)
        .eq('season', CURRENT_SEASON)
        .eq('vs_hand', 'A'),

      // Vs LHP / vs RHP splits, season window only
      supabase
        .from('batter_stats')
        .select('*')
        .eq('batter_id', playerId.value)
        .eq('season', CURRENT_SEASON)
        .eq('window_type', 'season')
        .in('vs_hand', ['L', 'R']),

      // Latest Combined Heat snapshot. Errors swallowed — a missing
      // batter_trends table (migration not yet applied) shouldn't crash
      // the whole page; the panel just won't render.
      supabase
        .from('batter_trends')
        .select('*')
        .eq('batter_id', playerId.value)
        .order('trend_date', { ascending: false })
        .limit(1)
        .then(r => r, e => ({ data: null, error: e })),
    ])

    // Sort windows into season/l30/l14/l7 order
    const wsByType = {}
    for (const r of (windowsRes.data || [])) wsByType[r.window_type] = r
    windows.value = WINDOW_ORDER
      .map(t => wsByType[t])
      .filter(Boolean)

    // Splits
    for (const s of (splitsRes.data || [])) {
      if (s.vs_hand === 'L') splitVsL.value = s
      else if (s.vs_hand === 'R') splitVsR.value = s
    }

    // Trends (non-fatal)
    if (trendsRes.error) {
      console.warn('[PlayerView] trend query failed (non-fatal):', trendsRes.error.message || trendsRes.error)
    } else {
      trendData.value = trendsRes.data?.[0] || null
    }

    // 3. Tonight's matchup — separate function with its own dependent
    //    sub-queries (games → lineups → pitcher_stats). Kept sequential
    //    inside because each one needs the previous result.
    await loadTonightMatchup()

    loading.value = false
  } catch (e) {
    console.error('[PlayerView] load failed:', e)
    error.value = e.message || String(e)
    loading.value = false
  }
}

async function loadTonightMatchup() {
  // DST-safe ET-relative "today" calculation. The previous version hardcoded
  // a -4h offset (EDT), which silently wrong-shifted by an hour from early
  // November through early March (EST = UTC-5). Using Intl.DateTimeFormat
  // with the IANA zone delegates the offset math to the runtime.
  function etDateIso(d) {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(d)
  }
  const now = new Date()
  const todayStr = etDateIso(now)
  const tomorrowStr = etDateIso(new Date(now.getTime() + 24 * 60 * 60 * 1000))

  // First, find upcoming game IDs (today/tomorrow ET, not final)
  const { data: candidateGames } = await supabase
    .from('games')
    .select(`
      id, mlb_game_pk, game_date, game_time_utc, venue, status,
      away_team_id, home_team_id,
      away_pitcher:players!games_away_pitcher_id_fkey ( id, mlbam_id, name, throws ),
      home_pitcher:players!games_home_pitcher_id_fkey ( id, mlbam_id, name, throws ),
      away_team:teams!games_away_team_id_fkey ( id, abbrev, name ),
      home_team:teams!games_home_team_id_fkey ( id, abbrev, name )
    `)
    .in('game_date', [todayStr, tomorrowStr])
    .not('status', 'in', '("Final","Game Over","Completed Early")')
    .order('game_time_utc', { ascending: true })

  if (!candidateGames || candidateGames.length === 0) return

  const candidateIds = candidateGames.map(g => g.id)

  // Find lineup row for this player in any of those games
  const { data: lineupRows } = await supabase
    .from('lineups')
    .select('id, batting_order, position, bats, is_confirmed, source, game_id, team_id')
    .eq('player_id', playerId.value)
    .in('game_id', candidateIds)
    .limit(5)

  if (!lineupRows || lineupRows.length === 0) return

  // Join lineup → game (lookup by game_id, take soonest)
  const gameById = {}
  for (const g of candidateGames) gameById[g.id] = g

  const enriched = lineupRows
    .map(lr => ({ lr, g: gameById[lr.game_id] }))
    .filter(x => x.g)
    .sort((a, b) => {
      // TBD games (null game_time_utc) sort LAST so we surface the soonest
      // scheduled one first. Without this guard, `new Date(null)` evaluates
      // to 1970-01-01 and TBD games would surface as the "soonest".
      const at = a.g.game_time_utc ? new Date(a.g.game_time_utc).getTime() : Infinity
      const bt = b.g.game_time_utc ? new Date(b.g.game_time_utc).getTime() : Infinity
      return at - bt
    })

  if (!enriched.length) return

  const { lr, g } = enriched[0]

  // Player's team vs opponent
  const isAway = lr.team_id === g.away_team_id
  const opponentPitcher = isAway ? g.home_pitcher : g.away_pitcher
  const opponentTeam = isAway ? g.home_team : g.away_team

  tonightGame.value = {
    game: g,
    lineupRow: lr,
    isAway,
    opponentPitcher,
    opponentTeam,
  }

  // Load pitcher allowed stats (L14) if we have a pitcher.
  // Wrapped in its own try/catch so a transient query failure (or rare
  // multi-row uniqueness conflict) doesn't crash the whole page — the
  // matchup card just won't show the pitcher-allowed strip.
  if (opponentPitcher?.id) {
    try {
      const { data: ps } = await supabase
        .from('pitcher_stats')
        .select('barrel_pct, hard_hit_pct, xba, xslg')
        .eq('pitcher_id', opponentPitcher.id)
        .eq('season', CURRENT_SEASON)
        .eq('window_type', 'l14')
        .maybeSingle()
      pitcherAllowed.value = ps || null
    } catch (e) {
      console.warn('[PlayerView] pitcher_allowed query failed (non-fatal):', e?.message || e)
      pitcherAllowed.value = null
    }
  }
}

onMounted(load)

// Re-load if route param changes (user navigates to another player without
// unmount). In practice App.vue's `:key="route.fullPath"` makes the component
// remount on navigation so this watch never actually fires — kept here as
// defensive coverage in case the :key is removed in the future.
watch(playerId, (newId, oldId) => {
  if (newId && newId !== oldId) {
    player.value = null
    windows.value = []
    splitVsL.value = null
    splitVsR.value = null
    tonightGame.value = null
    pitcherAllowed.value = null
    trendData.value = null
    load()
  }
})

// ── Countdown ticker for tonight's matchup ─────────────────────
const nowTick = ref(Date.now())
let tickInterval = null
onMounted(() => {
  tickInterval = setInterval(() => { nowTick.value = Date.now() }, 30000)
})
onUnmounted(() => { if (tickInterval) clearInterval(tickInterval) })

const gameCountdown = computed(() => {
  if (!tonightGame.value?.game?.game_time_utc) return null
  nowTick.value // dep
  return formatCountdown(tonightGame.value.game.game_time_utc)
})

const gameTime = computed(() => {
  if (!tonightGame.value?.game?.game_time_utc) return null
  return formatGameTime(tonightGame.value.game.game_time_utc)
})

// ── Trajectory data ────────────────────────────────────────────
// For each metric, build {window_type, value} array across windows
const TRAJECTORY_METRICS = [
  { key: 'barrel_pct',   label: 'Brl%',   term: 'barrel_pct'   },
  { key: 'hard_hit_pct', label: 'HH%',    term: 'hard_hit_pct' },
  { key: 'xslg',         label: 'xSLG',   term: 'xslg'         },
  { key: 'xba',          label: 'xBA',    term: 'xba'          },
  { key: 'xwoba',        label: 'xwOBA',  term: 'xwoba'        },
  { key: 'sweet_spot_pct', label: 'Sweet%', term: 'sweet_spot_pct' },
]

const trajectory = computed(() => {
  return TRAJECTORY_METRICS.map(m => {
    const points = WINDOW_ORDER.map(wt => {
      const w = windows.value.find(x => x.window_type === wt)
      return {
        window: wt,
        label: WINDOW_LABEL[wt],
        value: w?.[m.key] != null ? Number(w[m.key]) : null,
        pa: w?.pa,
        bbe: w?.bbe,
      }
    })

    // Compute trend: compare L7 vs Season
    const seasonVal = points[0].value
    const l7Val = points[3].value
    let trend = null
    if (seasonVal != null && l7Val != null && seasonVal !== 0) {
      const diffPct = ((l7Val - seasonVal) / seasonVal) * 100
      if (diffPct >= 15) trend = 'up'
      else if (diffPct <= -15) trend = 'down'
      else trend = 'flat'
    }

    // Max value across all points (for bar normalization)
    const validVals = points.map(p => p.value).filter(v => v != null)
    const maxVal = validVals.length ? Math.max(...validVals) : null

    return {
      ...m,
      points,
      trend,
      maxVal,
    }
  })
})

// ── Season summary (always the season row at vs_hand=A) ────────
const season = computed(() => {
  return windows.value.find(w => w.window_type === 'season') || null
})

// ── Combined Heat panel data ───────────────────────────────────
// Per-metric trend mini-cards, ordered by absolute magnitude so the
// strongest signal shows leftmost. Uses the same tier system as the
// Trends page so visual treatment is consistent.

// Tier thresholds (match useTrends.js)
function tierFromTs(ts) {
  if (ts == null) return 'flat'
  if (ts >= 0.50)  return 'blazing'
  if (ts >= 0.25)  return 'hot'
  if (ts >= 0.10)  return 'warm'
  if (ts <= -0.50) return 'frozen'
  if (ts <= -0.25) return 'cold'
  if (ts <= -0.10) return 'cool'
  return 'flat'
}

function tierLabel(ts) {
  if (ts == null) return '—'
  if (ts >= 0.50)  return 'BLAZING'
  if (ts >= 0.25)  return 'HOT'
  if (ts >= 0.10)  return 'WARM'
  if (ts <= -0.50) return 'FROZEN'
  if (ts <= -0.25) return 'COLD'
  if (ts <= -0.10) return 'COOL'
  return 'FLAT'
}

function fmtTrendPct(ts) {
  if (ts == null) return '—'
  const sign = ts > 0 ? '+' : ''
  return `${sign}${Math.round(ts * 100)}%`
}

const TREND_METRIC_LABELS = {
  hr:     { short: 'HR/PA',   label: 'Home Run Rate' },
  hits:   { short: 'H/PA',    label: 'Hit Rate'      },
  barrel: { short: 'Barrel%', label: 'Barrel Rate'   },
  iso:    { short: 'ISO',     label: 'Isolated Power' },
}

// Per-metric mini cards. Ordered: combined first, then base metrics by
// absolute trend magnitude (strongest signal first).
const trendMiniCards = computed(() => {
  if (!trendData.value) return []
  const base = ['hr', 'hits', 'barrel', 'iso'].map(m => ({
    key: m,
    short: TREND_METRIC_LABELS[m].short,
    label: TREND_METRIC_LABELS[m].label,
    ts: trendData.value[`${m}_trend`] != null ? Number(trendData.value[`${m}_trend`]) : null,
  }))
  // Strongest absolute trend first, nulls last
  base.sort((a, b) => {
    if (a.ts == null && b.ts == null) return 0
    if (a.ts == null) return 1
    if (b.ts == null) return -1
    return Math.abs(b.ts) - Math.abs(a.ts)
  })
  return base
})

// Combined card (the dominant signal at top of the panel)
const combinedTrend = computed(() => {
  if (!trendData.value) return null
  const ts = trendData.value.combined_trend
  return ts != null ? Number(ts) : null
})

// Was today's snapshot or older? Used to show "stale" warning if data
// is older than 24h (rare, but cron could fail).
//
// Depends on nowTick so the badge updates if the user keeps the tab open
// past the stale threshold. Without this dep, the computed wouldn't
// re-evaluate just because time passed.
const trendIsStale = computed(() => {
  if (!trendData.value?.trend_date) return false
  nowTick.value  // reactive dep — re-evaluate on each tick
  const td = new Date(trendData.value.trend_date + 'T00:00:00Z')
  const ageDays = (Date.now() - td.getTime()) / (1000 * 60 * 60 * 24)
  return ageDays > 1.5
})

// ── Pitch type breakdown ───────────────────────────────────────
// season.by_pitch_type is a JSONB object like:
// { "4SM": {pa, hr, hr_pct, ev_avg, brl_pct}, "SL": {...}, ... }
const pitchBreakdown = computed(() => {
  const bp = season.value?.by_pitch_type
  if (!bp || typeof bp !== 'object') return []

  return Object.entries(bp)
    .filter(([, stats]) => stats && typeof stats === 'object')
    .map(([code, stats]) => ({
      pitch: code,
      pa: stats.pa ?? null,
      hr: stats.hr ?? null,
      hr_pct: stats.hr_pct ?? null,
      ev_avg: stats.ev_avg ?? null,
      brl_pct: stats.brl_pct ?? null,
    }))
    .filter(p => p.pa && p.pa >= 5)
    .sort((a, b) => (b.pa || 0) - (a.pa || 0))
})

// ── Helpers ────────────────────────────────────────────────────
function fmtSeasonStat(value, kind) {
  if (value == null) return '—'
  if (kind === 'avg') return value < 1
    ? `.${Math.round(value * 1000).toString().padStart(3, '0')}`
    : value.toFixed(3)
  if (kind === 'int') return String(Math.round(value))
  if (kind === 'pct1') return `${Number(value).toFixed(1)}%`
  return String(value)
}

function barWidthPct(value, maxVal) {
  if (value == null || maxVal == null || maxVal === 0) return 0
  return Math.min((value / maxVal) * 100, 100)
}

// Map a stat-color text class to a guaranteed-safe bg class for the trajectory bars.
// Avoids relying on .replace('text-','bg-') which would break for tones that don't
// have a corresponding bg- utility defined in tailwind.config.
function toneBg(value, statKey, context = 'batter') {
  const cls = statColor(value, statKey, context)
  if (cls.includes('signal-400'))   return 'bg-signal-400'
  if (cls.includes('signal-200'))   return 'bg-signal-400/60'
  if (cls.includes('edge-cold-1'))  return 'bg-edge-cold-1'
  if (cls.includes('edge-cold-2'))  return 'bg-edge-cold-2'
  return 'bg-fg-500/40'
}

function hrPctTone(pct) {
  if (pct == null) return 'text-fg-500'
  if (pct >= 6) return 'text-signal-400'
  if (pct >= 4) return 'text-signal-200'
  if (pct >= 2) return 'text-fg-600'
  return 'text-edge-cold-1'
}
</script>

<template>
  <div class="min-h-screen">
    <div class="max-w-4xl mx-auto">

    <!-- ← back -->
    <div class="px-4 sm:px-6 pt-4 sm:pt-5 pb-2 flex items-center gap-4">
      <button
        @click="router.back()"
        class="label-caps hover:text-signal-400 transition inline-flex items-center gap-2 min-h-[36px] -my-2 px-1"
      >
        <span>←</span><span>back</span>
      </button>
      <span class="label-bracket !text-[9px] opacity-60">M.01.c · PLAYER</span>
    </div>

    <!-- Loading -->
    <LoadingBrand v-if="loading" text="Loading player…" />

    <!-- Error -->
    <div v-else-if="error" class="px-6 py-10">
      <div class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">player not found</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>
    </div>

    <template v-else-if="player">
      <!-- ── HEADER ───────────────────────────────────────── -->
      <header class="px-4 sm:px-6 pb-4 border-b border-bg-200">
        <div class="flex items-center gap-3 sm:gap-4">
          <img
            v-if="player.mlbam_id"
            :src="hiResHeadshotUrl(player.mlbam_id)"
            :alt="player.name"
            class="player-headshot-xl"
            @error="hideOnError"
          />
          <div class="flex-1 min-w-0">
            <div class="flex items-baseline gap-2 flex-wrap mb-1.5">
              <h1 class="display-text text-2xl sm:text-3xl text-fg-800 tracking-tight leading-none truncate">
                {{ player.name }}
              </h1>
              <!-- POD badge: prominent gold trophy tag when this player is today's pick -->
              <span
                v-if="isPOD(playerId)"
                class="display-num text-[10px] font-bold px-2 py-1 rounded-sm bg-amber-400/20 text-amber-300 border border-amber-400/40 leading-none whitespace-nowrap"
                title="Today's Play of the Day"
                aria-label="Today's Play of the Day"
              >★ POD</span>
            </div>
            <div class="flex items-baseline gap-2 sm:gap-3 flex-wrap">
              <span v-if="team" class="label-bracket text-signal-400">
                {{ team.abbrev }}
              </span>
              <span v-if="player.position" class="label-caps">
                {{ player.position }}
              </span>
              <span v-if="player.bats" class="font-mono text-[10px] text-fg-500">
                Bats {{ player.bats }}
              </span>
            </div>
          </div>
        </div>
      </header>

      <!-- ── TONIGHT'S MATCHUP (if any) ───────────────────── -->
      <section
        v-if="tonightGame"
        class="px-4 sm:px-6 py-4 border-b border-bg-200 bg-signal-400/5"
      >
        <div class="flex items-baseline justify-between mb-2 flex-wrap gap-2">
          <span class="label-bracket text-signal-400">tonight's matchup</span>
          <span class="display-num text-xs text-fg-500">
            {{ gameTime }}
            <span v-if="gameCountdown" class="text-signal-200 ml-2">({{ gameCountdown }})</span>
          </span>
        </div>

        <router-link
          :to="{ name: 'hr-report', params: { gameId: tonightGame.game.id } }"
          class="flex items-center gap-3 group"
        >
          <div class="flex-1">
            <div class="display-text text-base sm:text-lg text-fg-800 truncate">
              vs
              <span v-if="tonightGame.opponentPitcher" class="text-signal-200 group-hover:underline">
                {{ tonightGame.opponentPitcher.name }}
              </span>
              <span v-else class="italic text-fg-500">TBD</span>
              <span class="label-caps ml-2">{{ tonightGame.opponentTeam?.abbrev }}</span>
              <span v-if="tonightGame.opponentPitcher?.throws"
                    class="label-caps !text-[9px] ml-1 opacity-70">
                throws {{ tonightGame.opponentPitcher.throws }}HP
              </span>
            </div>
            <div class="label-caps mt-1 inline-flex items-center gap-2">
              <span>batting</span>
              <span class="display-num text-signal-400">#{{ tonightGame.lineupRow.batting_order }}</span>
              <span
                class="!text-[8px] px-1.5 py-0.5 rounded-sm"
                :class="tonightGame.lineupRow.is_confirmed
                  ? 'text-signal-400 bg-signal-400/10'
                  : tonightGame.lineupRow.source === 'last_known'
                    ? 'text-amber-300 bg-amber-500/10'
                    : 'text-fg-500 bg-bg-200/60'"
              >
                {{ tonightGame.lineupRow.is_confirmed
                    ? 'confirmed'
                    : tonightGame.lineupRow.source === 'last_known'
                      ? 'projected · last lineup'
                      : 'projected' }}
              </span>
            </div>
          </div>
          <span class="text-fg-500 group-hover:text-signal-400 transition">→</span>
        </router-link>

        <!-- Pitcher allowed mini-strip -->
        <div
          v-if="pitcherAllowed"
          class="mt-3 pt-3 border-t border-bg-200/40 grid grid-cols-4 gap-2"
        >
          <div class="flex flex-col gap-0.5">
            <span class="label-caps !text-[8px] inline-flex items-center">
              HH% allowed <InfoTooltip term="pitcher_hard_hit_pct" />
            </span>
            <span
              class="display-num text-sm font-medium"
              :class="statColor(pitcherAllowed.hard_hit_pct, 'hard_hit_pct', 'pitcher')"
            >
              {{ fmtStat(pitcherAllowed.hard_hit_pct, 'hard_hit_pct') }}
            </span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="label-caps !text-[8px] inline-flex items-center">
              Brl% allowed <InfoTooltip term="pitcher_barrel_pct" />
            </span>
            <span
              class="display-num text-sm font-medium"
              :class="statColor(pitcherAllowed.barrel_pct, 'barrel_pct', 'pitcher')"
            >
              {{ fmtStat(pitcherAllowed.barrel_pct, 'barrel_pct') }}
            </span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="label-caps !text-[8px] inline-flex items-center">
              xSLG allowed <InfoTooltip term="pitcher_xslg" />
            </span>
            <span
              class="display-num text-sm font-medium"
              :class="statColor(pitcherAllowed.xslg, 'xslg', 'pitcher')"
            >
              {{ fmtStat(pitcherAllowed.xslg, 'xslg') }}
            </span>
          </div>
          <div class="flex flex-col gap-0.5">
            <span class="label-caps !text-[8px] inline-flex items-center">
              xBA allowed <InfoTooltip term="pitcher_xba" />
            </span>
            <span
              class="display-num text-sm font-medium"
              :class="statColor(pitcherAllowed.xba, 'xba', 'pitcher')"
            >
              {{ fmtStat(pitcherAllowed.xba, 'xba') }}
            </span>
          </div>
        </div>
      </section>

      <!-- ── SEASON QUICK STATS ──────────────────────────── -->
      <section v-if="season" class="px-4 sm:px-6 py-4 border-b border-bg-200">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="label-bracket text-signal-400">season {{ CURRENT_SEASON }}</h2>
          <span class="label-caps !text-[8px]">{{ season.pa }} PA</span>
        </div>
        <div class="grid grid-cols-3 sm:grid-cols-6 gap-x-4 gap-y-3">
          <div>
            <div class="label-caps">PA</div>
            <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
              {{ fmtSeasonStat(season.pa, 'int') }}
            </div>
          </div>
          <div>
            <div class="label-caps">HR</div>
            <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
              {{ fmtSeasonStat(season.hr, 'int') }}
            </div>
          </div>
          <div>
            <div class="label-caps">AVG</div>
            <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
              {{ fmtSeasonStat(season.avg, 'avg') }}
            </div>
          </div>
          <div>
            <div class="label-caps">OBP</div>
            <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
              {{ fmtSeasonStat(season.obp, 'avg') }}
            </div>
          </div>
          <div>
            <div class="label-caps">SLG</div>
            <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
              {{ fmtSeasonStat(season.slg, 'avg') }}
            </div>
          </div>
          <div>
            <div class="label-caps">ISO</div>
            <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
              {{ fmtSeasonStat(season.iso, 'avg') }}
            </div>
          </div>
        </div>
      </section>

      <!-- ── COMBINED HEAT ───────────────────────────────────
           Cebolla's multi-signal trend score. Shows the player's
           Combined Heat alongside the four base metrics so users can
           see WHY they're trending (or whether the signal is real). -->
      <section
        v-if="trendData"
        class="px-4 sm:px-6 py-4 border-b border-bg-200"
      >
        <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
          <h2 class="label-bracket text-signal-400">combined heat</h2>
          <span class="label-caps !text-[8px] opacity-70">
            L14 vs season · multi-signal
            <span v-if="trendIsStale" class="text-amber-400 ml-2">
              · snapshot is stale
            </span>
          </span>
        </div>

        <!-- Dominant: combined chip + tier label -->
        <div class="flex items-stretch gap-3 mb-4 flex-wrap">
          <div
            class="trend-hero-chip"
            :class="`trend-hero-chip--${tierFromTs(combinedTrend)}`"
          >
            <div class="trend-hero-chip__pct display-num">
              {{ fmtTrendPct(combinedTrend) }}
            </div>
            <div class="trend-hero-chip__label">
              {{ tierLabel(combinedTrend) }}
            </div>
          </div>
          <div class="flex-1 min-w-[200px] flex flex-col justify-center text-fg-500 text-xs sm:text-sm leading-relaxed">
            <span v-if="combinedTrend == null">
              Not enough data across multiple metrics to compute combined heat.
            </span>
            <span v-else-if="combinedTrend >= 0.25">
              Multi-signal heat — this player is trending up on multiple
              independent stats simultaneously, not just one lucky metric.
            </span>
            <span v-else-if="combinedTrend <= -0.25">
              Multi-signal cool-down — multiple metrics softening at once.
            </span>
            <span v-else>
              Roughly aligned with season baseline across signals.
            </span>
          </div>
        </div>

        <!-- Per-metric mini cards (ordered by signal strength) -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <div
            v-for="m in trendMiniCards"
            :key="m.key"
            class="trend-mini"
            :class="`trend-mini--${tierFromTs(m.ts)}`"
          >
            <div class="label-caps !text-[8px] opacity-75">{{ m.short }}</div>
            <div class="display-num text-base sm:text-lg mt-1">
              {{ fmtTrendPct(m.ts) }}
            </div>
            <div class="text-[8px] font-mono uppercase tracking-wider opacity-70 mt-0.5">
              {{ tierLabel(m.ts) }}
            </div>
          </div>
        </div>
      </section>

      <!-- ── STATCAST TRAJECTORY ─────────────────────────── -->
      <section class="px-4 sm:px-6 py-4 border-b border-bg-200">
        <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
          <h2 class="label-bracket text-signal-400">statcast trajectory</h2>
          <span class="label-caps !text-[8px] opacity-70">
            Season → L30 → L14 → L7
          </span>
        </div>

        <div v-if="!windows.length" class="text-fg-500 italic text-sm py-3">
          No Statcast data available yet for this player this season.
        </div>

        <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <div
            v-for="m in trajectory"
            :key="m.key"
            class="bg-bg-50 border border-bg-200 px-3 py-2.5"
          >
            <div class="flex items-baseline justify-between mb-1.5">
              <span class="label-caps inline-flex items-center">
                {{ m.label }} <InfoTooltip :term="m.term" />
              </span>
              <span
                v-if="m.trend"
                class="display-num text-[10px]"
                :class="m.trend === 'up'   ? 'text-signal-400'
                      : m.trend === 'down' ? 'text-edge-cold-1'
                      :                      'text-fg-500'"
              >
                {{ m.trend === 'up' ? '↗' : m.trend === 'down' ? '↘' : '→' }}
              </span>
            </div>
            <div class="space-y-1">
              <div
                v-for="pt in m.points"
                :key="pt.window"
                class="grid grid-cols-[36px_1fr_44px] gap-2 items-center"
              >
                <span class="label-caps !text-[8px]">{{ pt.label }}</span>
                <div class="h-1.5 bg-bg-200 relative overflow-hidden">
                  <div
                    class="absolute inset-y-0 left-0 transition-all duration-300"
                    :class="pt.value == null
                      ? 'bg-bg-300/30'
                      : toneBg(pt.value, m.key, 'batter')"
                    :style="{ width: `${barWidthPct(pt.value, m.maxVal)}%` }"
                  ></div>
                </div>
                <span
                  class="display-num text-[11px] text-right"
                  :class="statColor(pt.value, m.key, 'batter')"
                >
                  {{ fmtStat(pt.value, m.key) }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ── VS LHP / VS RHP SPLITS ──────────────────────── -->
      <section
        v-if="splitVsL || splitVsR"
        class="px-4 sm:px-6 py-4 border-b border-bg-200"
      >
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="label-bracket text-signal-400">handedness splits</h2>
          <span class="label-caps !text-[8px] opacity-70">season</span>
        </div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <!-- vs LHP -->
          <div class="bg-bg-50 border border-bg-200 px-3 sm:px-4 py-3">
            <div class="flex items-baseline justify-between mb-2">
              <span class="label-bracket text-signal-400">vs LHP</span>
              <span v-if="splitVsL" class="label-caps !text-[8px]">{{ splitVsL.pa }} PA</span>
            </div>
            <div v-if="splitVsL" class="grid grid-cols-4 gap-2">
              <div>
                <div class="label-caps !text-[8px]">HR</div>
                <div class="display-num text-sm text-fg-700">{{ splitVsL.hr }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">AVG</div>
                <div class="display-num text-sm text-fg-700">{{ fmtSeasonStat(splitVsL.avg, 'avg') }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">SLG</div>
                <div class="display-num text-sm text-fg-700">{{ fmtSeasonStat(splitVsL.slg, 'avg') }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">Brl%</div>
                <div
                  class="display-num text-sm"
                  :class="statColor(splitVsL.barrel_pct, 'barrel_pct', 'batter')"
                >
                  {{ fmtStat(splitVsL.barrel_pct, 'barrel_pct') }}
                </div>
              </div>
            </div>
            <div v-else class="text-fg-500 italic text-xs">No data</div>
          </div>

          <!-- vs RHP -->
          <div class="bg-bg-50 border border-bg-200 px-3 sm:px-4 py-3">
            <div class="flex items-baseline justify-between mb-2">
              <span class="label-bracket text-signal-400">vs RHP</span>
              <span v-if="splitVsR" class="label-caps !text-[8px]">{{ splitVsR.pa }} PA</span>
            </div>
            <div v-if="splitVsR" class="grid grid-cols-4 gap-2">
              <div>
                <div class="label-caps !text-[8px]">HR</div>
                <div class="display-num text-sm text-fg-700">{{ splitVsR.hr }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">AVG</div>
                <div class="display-num text-sm text-fg-700">{{ fmtSeasonStat(splitVsR.avg, 'avg') }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">SLG</div>
                <div class="display-num text-sm text-fg-700">{{ fmtSeasonStat(splitVsR.slg, 'avg') }}</div>
              </div>
              <div>
                <div class="label-caps !text-[8px]">Brl%</div>
                <div
                  class="display-num text-sm"
                  :class="statColor(splitVsR.barrel_pct, 'barrel_pct', 'batter')"
                >
                  {{ fmtStat(splitVsR.barrel_pct, 'barrel_pct') }}
                </div>
              </div>
            </div>
            <div v-else class="text-fg-500 italic text-xs">No data</div>
          </div>
        </div>
      </section>

      <!-- ── PITCH TYPE BREAKDOWN ───────────────────────── -->
      <section v-if="pitchBreakdown.length" class="px-4 sm:px-6 py-4 pb-8">
        <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
          <h2 class="label-bracket text-signal-400">pitch-type breakdown</h2>
          <span class="label-caps !text-[8px] opacity-70">
            season only · rolling-window breakdowns coming soon
          </span>
        </div>

        <div class="bg-bg-50 border border-bg-200 overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left">
                <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Pitch</th>
                <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">PA</th>
                <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">HR</th>
                <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">HR%</th>
                <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">EV</th>
                <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Brl%</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in pitchBreakdown" :key="p.pitch" class="hover:bg-bg-100/40">
                <td class="py-2 px-3 border-b border-bg-200/40 display-num text-xs text-fg-700 font-medium">
                  {{ p.pitch }}
                </td>
                <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-600">
                  {{ p.pa }}
                </td>
                <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-600">
                  {{ p.hr }}
                </td>
                <td
                  class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs"
                  :class="hrPctTone(p.hr_pct)"
                >
                  {{ p.hr_pct != null ? p.hr_pct.toFixed(1) : '—' }}
                </td>
                <td class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs text-fg-600">
                  {{ p.ev_avg != null ? p.ev_avg.toFixed(1) : '—' }}
                </td>
                <td
                  class="py-2 px-2 border-b border-bg-200/40 text-right display-num text-xs"
                  :class="statColor(p.brl_pct, 'barrel_pct', 'batter')"
                >
                  {{ p.brl_pct != null ? fmtStat(p.brl_pct, 'barrel_pct') : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- Empty state for pitch breakdown -->
      <section v-else-if="season" class="px-4 sm:px-6 py-4 pb-8">
        <div class="flex items-baseline justify-between mb-3">
          <h2 class="label-bracket text-signal-400">pitch-type breakdown</h2>
        </div>
        <div class="bg-bg-50 border border-bg-200 px-4 py-6 text-center">
          <div class="text-fg-500 text-xs italic">
            Not enough at-bats yet to break down by pitch type.
          </div>
        </div>
      </section>
    </template>

    <!-- No-player-found fallback -->
    <div v-else class="px-6 py-20 text-center">
      <div class="display-text text-2xl text-fg-500 italic mb-2">Player not found</div>
      <p class="text-fg-500 text-xs">
        ID <span class="display-num">{{ playerId }}</span> isn't in the database.
      </p>
      <router-link to="/" class="inline-block mt-6 label-caps hover:text-signal-400 transition">
        ← back to slate
      </router-link>
    </div>

    </div><!-- /max-w-4xl -->
  </div>
</template>

<style scoped>
.player-headshot-xl {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  filter: grayscale(0.15) brightness(0.97);
  border: 1px solid rgba(255, 255, 255, 0.06);
}
@media (min-width: 640px) {
  .player-headshot-xl {
    width: 72px;
    height: 72px;
  }
}

/* ── Combined Heat panel ──────────────────────────────────────── */
.trend-hero-chip {
  border: 1px solid;
  padding: 14px 22px;
  min-width: 120px;
  text-align: center;
  border-radius: 2px;
  color: var(--chip-color, #9B9BA8);
  background: var(--chip-bg, rgba(38, 38, 46, 0.5));
  border-color: var(--chip-border, #33333D);
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.trend-hero-chip__pct {
  font-size: 28px;
  font-weight: 600;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.trend-hero-chip__label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.18em;
  margin-top: 6px;
  opacity: 0.85;
}

.trend-mini {
  padding: 8px 10px;
  border: 1px solid;
  border-radius: 2px;
  color: var(--chip-color, #9B9BA8);
  background: var(--chip-bg, rgba(38, 38, 46, 0.4));
  border-color: var(--chip-border, #33333D);
  text-align: left;
}

/* Tier color tokens — match TrendCard/TrendRowCompact exactly */
.trend-hero-chip--blazing, .trend-mini--blazing {
  --chip-color: #FFE5E5;
  --chip-bg: rgba(255, 42, 42, 0.18);
  --chip-border: rgba(255, 42, 42, 0.65);
}
.trend-hero-chip--hot, .trend-mini--hot {
  --chip-color: #FFB8B8;
  --chip-bg: rgba(255, 42, 42, 0.12);
  --chip-border: rgba(255, 42, 42, 0.45);
}
.trend-hero-chip--warm, .trend-mini--warm {
  --chip-color: #FFD3A8;
  --chip-bg: rgba(255, 168, 74, 0.10);
  --chip-border: rgba(255, 168, 74, 0.40);
}
.trend-hero-chip--flat, .trend-mini--flat {
  --chip-color: #9B9BA8;
  --chip-bg: rgba(38, 38, 46, 0.5);
  --chip-border: #33333D;
}
.trend-hero-chip--cool, .trend-mini--cool {
  --chip-color: #A8D9EE;
  --chip-bg: rgba(79, 177, 221, 0.08);
  --chip-border: rgba(79, 177, 221, 0.35);
}
.trend-hero-chip--cold, .trend-mini--cold {
  --chip-color: #4FB1DD;
  --chip-bg: rgba(58, 141, 188, 0.12);
  --chip-border: rgba(58, 141, 188, 0.45);
}
.trend-hero-chip--frozen, .trend-mini--frozen {
  --chip-color: #7CC8E5;
  --chip-bg: rgba(58, 141, 188, 0.18);
  --chip-border: rgba(58, 141, 188, 0.65);
}
</style>
