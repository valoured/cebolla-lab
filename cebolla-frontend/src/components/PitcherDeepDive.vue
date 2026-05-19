<script setup>
/**
 * PitcherDeepDive.vue — Pitcher Deep Dive panel
 *
 * Mounted from PlayerView when the loaded player is a pitcher.
 * Shows:
 *   1. Tonight's start (if any) — game card with countdown, links to HR Report
 *   2. Season summary — traditional stats: GS, IP, ERA, FIP, WHIP, K/9, BB/9, HR/9, HR/PA
 *   3. Pitcher-allowed Statcast trajectory — Brl%, HH%, xSLG, xBA across Season → L30 → L14 → L7
 *      Uses 'pitcher' color context: red = elite (suppressing contact), blue = getting hit hard.
 *      Trend arrows mirror the batter view: ↗ up, ↘ down, → flat — but interpretation flips:
 *      ↗ on Brl% allowed means the pitcher is trending WORSE (giving up more barrels).
 *      The trajectory section header notes this.
 *
 * Stage 2 (separate ship): arsenal grid.
 *
 * Props:
 *   - player: { id, mlbam_id, name, position, throws, ... }
 *   - team:   { id, abbrev, name, stadium } | null
 *
 * Loads its own data (windows + tonight's start) keyed by player.id.
 */

import { ref, computed, onMounted, watch, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { statColor, fmtStat } from '../utils/percentileColors.js'
import { formatGameTime, formatCountdown } from '../utils/timeHelpers.js'
import InfoTooltip from '../components/InfoTooltip.vue'

const props = defineProps({
  player: { type: Object, required: true },
  team:   { type: Object, default: null },
})

const CURRENT_SEASON = new Date().getFullYear()

const WINDOW_ORDER = ['season', 'l30', 'l14', 'l7']
const WINDOW_LABEL = {
  season: 'Season',
  l30:    'L30',
  l14:    'L14',
  l7:     'L7',
}

// ── Local state ─────────────────────────────────────────────────
const windows = ref([])           // pitcher_stats rows, one per window_type
const arsenal = ref([])           // pitcher_arsenals rows (season, both stances)
const tonightGame = ref(null)     // { game, opponentTeam, isHomePitcher }
const loading = ref(true)
const error = ref(null)

// ── Data load ───────────────────────────────────────────────────
async function load() {
  loading.value = true
  error.value = null
  try {
    // 1. All pitcher_stats rows for this player this season
    const { data: ws, error: wErr } = await supabase
      .from('pitcher_stats')
      .select('*')
      .eq('pitcher_id', props.player.id)
      .eq('season', CURRENT_SEASON)

    if (wErr) throw wErr

    const byType = {}
    for (const r of (ws || [])) byType[r.window_type] = r
    windows.value = WINDOW_ORDER.map(t => byType[t]).filter(Boolean)

    // 2. Arsenal — season only, both stances
    const { data: ars } = await supabase
      .from('pitcher_arsenals')
      .select('*')
      .eq('pitcher_id', props.player.id)
      .eq('season', CURRENT_SEASON)
      .eq('window_type', 'season')

    arsenal.value = ars || []

    // 3. Tonight's start (if any)
    await loadTonightStart()

    loading.value = false
  } catch (e) {
    console.error('[PitcherDeepDive] load failed:', e)
    error.value = e.message || String(e)
    loading.value = false
  }
}

async function loadTonightStart() {
  // ET-relative "today" so 8pm-midnight doesn't roll forward like UTC does
  const now = new Date()
  const offsetMs = -4 * 60 * 60 * 1000  // ET = UTC - 4 (EDT). Matches PlayerView convention.
  const etNow = new Date(now.getTime() + offsetMs)
  const todayStr = etNow.toISOString().slice(0, 10)
  const tomorrowStr = new Date(etNow.getTime() + 24 * 60 * 60 * 1000)
    .toISOString().slice(0, 10)

  // Find any non-final game where this player is the home or away starter
  const pid = props.player.id
  const { data: games } = await supabase
    .from('games')
    .select(`
      id, mlb_game_pk, game_date, game_time_utc, venue, status,
      away_team_id, home_team_id, away_pitcher_id, home_pitcher_id,
      away_team:teams!games_away_team_id_fkey ( id, abbrev, name ),
      home_team:teams!games_home_team_id_fkey ( id, abbrev, name )
    `)
    .in('game_date', [todayStr, tomorrowStr])
    .not('status', 'in', '("Final","Game Over","Completed Early")')
    .or(`away_pitcher_id.eq.${pid},home_pitcher_id.eq.${pid}`)
    .order('game_time_utc', { ascending: true })
    .limit(1)

  if (!games || games.length === 0) {
    tonightGame.value = null
    return
  }

  const g = games[0]
  const isHomePitcher = g.home_pitcher_id === pid
  const opponentTeam = isHomePitcher ? g.away_team : g.home_team

  tonightGame.value = {
    game: g,
    opponentTeam,
    isHomePitcher,
  }
}

onMounted(load)

// Re-load when the pitcher swaps (parent route param changes)
watch(() => props.player?.id, (newId, oldId) => {
  if (newId && newId !== oldId) {
    windows.value = []
    arsenal.value = []
    tonightGame.value = null
    load()
  }
})

// ── Countdown ticker ────────────────────────────────────────────
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

// ── Season summary ──────────────────────────────────────────────
const season = computed(() => {
  return windows.value.find(w => w.window_type === 'season') || null
})

// ── Statcast trajectory (pitcher-allowed) ──────────────────────
// Four metrics mirror the batter view. Color context = 'pitcher' (flipped).
const TRAJECTORY_METRICS = [
  { key: 'barrel_pct',   label: 'Brl%',  term: 'pitcher_barrel_pct'   },
  { key: 'hard_hit_pct', label: 'HH%',   term: 'pitcher_hard_hit_pct' },
  { key: 'xslg',         label: 'xSLG',  term: 'pitcher_xslg'         },
  { key: 'xba',          label: 'xBA',   term: 'pitcher_xba'          },
]

const trajectory = computed(() => {
  return TRAJECTORY_METRICS.map(m => {
    const points = WINDOW_ORDER.map(wt => {
      const w = windows.value.find(x => x.window_type === wt)
      return {
        window: wt,
        label: WINDOW_LABEL[wt],
        value: w?.[m.key] != null ? Number(w[m.key]) : null,
        bbe: w?.bbe,
      }
    })

    // Trend: compare L7 vs Season. For pitcher-allowed stats, higher = worse.
    // We tag 'down' = good direction (allowing less), 'up' = bad direction.
    // The arrow icon stays directional; meaning is conveyed by the section header note.
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

    return { ...m, points, trend, maxVal }
  })
})

// ── Arsenal table ──────────────────────────────────────────────
// pitcher_arsenals stores one row per (pitch_type × vs_stance). We pivot
// to one row per pitch_type with vsL and vsR sub-objects so the template
// can render side-by-side stance splits.
const ARSENAL_PITCH_LABELS = {
  '4SM': '4-Seam',
  'SI':  'Sinker',
  'CT':  'Cutter',
  'CH':  'Change',
  'SL':  'Slider',
  'CU':  'Curve',
  'KC':  'Knuck-C',
  'FS':  'Splitter',
  'SW':  'Sweeper',
  'ST':  'Sweeper',
  'KN':  'Knuckle',
}

const pitchTable = computed(() => {
  const byPitch = {}
  for (const r of arsenal.value) {
    const key = r.pitch_type
    if (!byPitch[key]) byPitch[key] = { pitch_type: key, vsL: null, vsR: null }
    if (r.vs_stance === 'L') byPitch[key].vsL = r
    else if (r.vs_stance === 'R') byPitch[key].vsR = r
  }

  return Object.values(byPitch)
    .map(p => ({
      ...p,
      label: ARSENAL_PITCH_LABELS[p.pitch_type] || p.pitch_type,
      // Total usage = sum of stance usages, used for sort order. Each
      // stance's usage_pct is per-stance, so summing approximates "how
      // important is this pitch overall."
      sortKey: (p.vsL?.usage_pct || 0) + (p.vsR?.usage_pct || 0),
    }))
    // Filter out pitches with negligible usage on both sides
    .filter(p => (p.vsL?.usage_pct || 0) >= 1 || (p.vsR?.usage_pct || 0) >= 1)
    .sort((a, b) => b.sortKey - a.sortKey)
})

// Tone for whiff% — higher is better for the pitcher
function whiffTone(pct) {
  if (pct == null) return 'text-fg-500'
  if (pct >= 35) return 'text-signal-400'
  if (pct >= 28) return 'text-signal-200'
  if (pct >= 20) return 'text-fg-600'
  return 'text-edge-cold-1'
}

// ── Helpers ────────────────────────────────────────────────────
function fmtSeasonStat(value, kind) {
  if (value == null) return '—'
  if (kind === 'avg') return value < 1
    ? `.${Math.round(value * 1000).toString().padStart(3, '0')}`
    : value.toFixed(3)
  if (kind === 'int')   return String(Math.round(value))
  if (kind === 'rate2') return Number(value).toFixed(2)
  if (kind === 'ip')    return Number(value).toFixed(1)
  if (kind === 'pct4')  return Number(value).toFixed(4)
  return String(value)
}

function barWidthPct(value, maxVal) {
  if (value == null || maxVal == null || maxVal === 0) return 0
  return Math.min((value / maxVal) * 100, 100)
}

// Map stat-color text class → safe bg class for trajectory bars.
// Pitcher context: red tones for elite (low values), cold tones for poor (high values).
function toneBg(value, statKey) {
  const cls = statColor(value, statKey, 'pitcher')
  if (cls.includes('signal-400'))   return 'bg-signal-400'
  if (cls.includes('signal-200'))   return 'bg-signal-400/60'
  if (cls.includes('edge-cold-1'))  return 'bg-edge-cold-1'
  if (cls.includes('edge-cold-2'))  return 'bg-edge-cold-2'
  return 'bg-fg-500/40'
}

// Has-data shortcuts for v-if gating of sections
const hasAnyData = computed(() => !!season.value || windows.value.length > 0)
</script>

<template>
  <div>
    <!-- ── TONIGHT'S START (if any) ───────────────────── -->
    <section
      v-if="tonightGame"
      class="px-4 sm:px-6 py-4 border-b border-bg-200 bg-signal-400/5"
    >
      <div class="flex items-baseline justify-between mb-2 flex-wrap gap-2">
        <span class="label-bracket text-signal-400">tonight's start</span>
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
            vs <span class="label-caps ml-1">{{ tonightGame.opponentTeam?.abbrev }}</span>
            <span class="label-caps !text-[9px] ml-2 opacity-70">
              {{ tonightGame.isHomePitcher ? 'home' : 'away' }}
            </span>
          </div>
          <div class="label-caps mt-1 inline-flex items-center gap-2">
            <span>{{ tonightGame.game.venue || '—' }}</span>
            <span
              v-if="tonightGame.game.status"
              class="!text-[8px] px-1.5 py-0.5 rounded-sm text-fg-500 bg-bg-200/60"
            >
              {{ tonightGame.game.status }}
            </span>
          </div>
        </div>
        <span class="text-fg-500 group-hover:text-signal-400 transition">→</span>
      </router-link>
    </section>

    <!-- ── SEASON SUMMARY ──────────────────────────────── -->
    <section v-if="season" class="px-4 sm:px-6 py-4 border-b border-bg-200">
      <div class="flex items-baseline justify-between mb-3">
        <h2 class="label-bracket text-signal-400">season {{ CURRENT_SEASON }}</h2>
        <span v-if="season.batters_faced" class="label-caps !text-[8px]">
          {{ season.batters_faced }} BF
        </span>
      </div>
      <!-- Two rows on mobile-ish, single row on desktop -->
      <div class="grid grid-cols-3 sm:grid-cols-6 gap-x-4 gap-y-3">
        <div>
          <div class="label-caps">GS</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.games_started, 'int') }}
          </div>
        </div>
        <div>
          <div class="label-caps">IP</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.innings_pitched, 'ip') }}
          </div>
        </div>
        <div>
          <div class="label-caps">ERA</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.era, 'rate2') }}
          </div>
        </div>
        <div>
          <div class="label-caps">FIP</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.fip, 'rate2') }}
          </div>
        </div>
        <div>
          <div class="label-caps">WHIP</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.whip, 'rate2') }}
          </div>
        </div>
        <div>
          <div class="label-caps">HR/9</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.hr_per_9, 'rate2') }}
          </div>
        </div>
        <div>
          <div class="label-caps">K/9</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.k_per_9, 'rate2') }}
          </div>
        </div>
        <div>
          <div class="label-caps">BB/9</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.bb_per_9, 'rate2') }}
          </div>
        </div>
        <div>
          <div class="label-caps">HR</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.hr_allowed, 'int') }}
          </div>
        </div>
        <div>
          <div class="label-caps">BB</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.bb, 'int') }}
          </div>
        </div>
        <div>
          <div class="label-caps">SO</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.so, 'int') }}
          </div>
        </div>
        <div>
          <div class="label-caps">HR/PA</div>
          <div class="display-num text-base sm:text-lg text-fg-700 mt-0.5">
            {{ fmtSeasonStat(season.hr_per_pa, 'pct4') }}
          </div>
        </div>
      </div>
    </section>

    <!-- ── STATCAST TRAJECTORY (pitcher-allowed) ───────── -->
    <section class="px-4 sm:px-6 py-4 border-b border-bg-200">
      <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h2 class="label-bracket text-signal-400">statcast trajectory · allowed</h2>
        <span class="label-caps !text-[8px] opacity-70">
          Season → L30 → L14 → L7 · red = elite suppressing
        </span>
      </div>

      <div v-if="!windows.length" class="text-fg-500 italic text-sm py-3">
        No Statcast data available yet for this pitcher this season.
      </div>

      <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        <div
          v-for="m in trajectory"
          :key="m.key"
          class="bg-bg-50 border border-bg-200 px-3 py-2.5"
        >
          <div class="flex items-baseline justify-between mb-1.5">
            <span class="label-caps inline-flex items-center">
              {{ m.label }} allowed <InfoTooltip :term="m.term" />
            </span>
            <span
              v-if="m.trend"
              class="display-num text-[10px]"
              :class="m.trend === 'up'   ? 'text-edge-cold-1'
                    : m.trend === 'down' ? 'text-signal-400'
                    :                      'text-fg-500'"
              :title="m.trend === 'up'
                ? 'Trending higher → worse for pitcher'
                : m.trend === 'down'
                  ? 'Trending lower → better for pitcher'
                  : 'Flat'"
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
                    : toneBg(pt.value, m.key)"
                  :style="{ width: `${barWidthPct(pt.value, m.maxVal)}%` }"
                ></div>
              </div>
              <span
                class="display-num text-[11px] text-right"
                :class="statColor(pt.value, m.key, 'pitcher')"
              >
                {{ fmtStat(pt.value, m.key) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ── ARSENAL · vs L / vs R ──────────────────────── -->
    <section v-if="pitchTable.length" class="px-4 sm:px-6 py-4 pb-8">
      <div class="flex items-baseline justify-between mb-3 flex-wrap gap-2">
        <h2 class="label-bracket text-signal-400">arsenal · vs L / vs R</h2>
        <span class="label-caps !text-[8px] opacity-70">
          season · per-stance usage
        </span>
      </div>

      <!-- Two stacked split panels: vs L on top, vs R below.
           Side-by-side at md+ so desktop shows the split at a glance. -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">

        <!-- ─ vs LHB ─ -->
        <div class="bg-bg-50 border border-bg-200">
          <div class="px-3 py-2 border-b border-bg-200 flex items-baseline justify-between">
            <span class="label-bracket text-signal-400">vs LHB</span>
            <span class="label-caps !text-[8px] opacity-70">
              {{ pitchTable.filter(p => p.vsL).length }} pitch{{ pitchTable.filter(p => p.vsL).length === 1 ? '' : 'es' }}
            </span>
          </div>

          <!-- column headers -->
          <div class="px-3 py-1.5 grid grid-cols-[44px_1fr_38px_38px_38px_38px] gap-1.5 border-b border-bg-200/60">
            <span class="label-caps !text-[8px]">Pitch</span>
            <span class="label-caps !text-[8px]">Usage</span>
            <span class="label-caps !text-[8px] text-right">Velo</span>
            <span class="label-caps !text-[8px] text-right">Brl%</span>
            <span class="label-caps !text-[8px] text-right">HH%</span>
            <span class="label-caps !text-[8px] text-right">Whf%</span>
          </div>

          <div
            v-for="p in pitchTable"
            :key="`l-${p.pitch_type}`"
            class="px-3 py-1.5 grid grid-cols-[44px_1fr_38px_38px_38px_38px] gap-1.5 items-center border-b border-bg-200/40 last:border-0"
          >
            <span class="display-num text-xs text-fg-700 font-medium">{{ p.pitch_type }}</span>

            <!-- Usage bar + value -->
            <div v-if="p.vsL" class="flex items-center gap-2 min-w-0">
              <div class="flex-1 h-1.5 bg-bg-200 relative overflow-hidden">
                <div
                  class="absolute inset-y-0 left-0 bg-signal-400/70"
                  :style="{ width: `${Math.min(p.vsL.usage_pct || 0, 100)}%` }"
                ></div>
              </div>
              <span class="display-num text-[10px] text-fg-500 w-7 text-right">
                {{ p.vsL.usage_pct != null ? p.vsL.usage_pct.toFixed(0) + '%' : '—' }}
              </span>
            </div>
            <span v-else class="text-fg-500 text-[10px] italic">—</span>

            <span class="display-num text-[11px] text-fg-600 text-right">
              {{ p.vsL?.velo_avg != null ? p.vsL.velo_avg.toFixed(0) : '—' }}
            </span>
            <span
              class="display-num text-[11px] text-right"
              :class="statColor(p.vsL?.barrel_pct, 'barrel_pct', 'pitcher')"
            >
              {{ p.vsL?.barrel_pct != null ? p.vsL.barrel_pct.toFixed(1) : '—' }}
            </span>
            <span
              class="display-num text-[11px] text-right"
              :class="statColor(p.vsL?.hard_hit_pct, 'hard_hit_pct', 'pitcher')"
            >
              {{ p.vsL?.hard_hit_pct != null ? p.vsL.hard_hit_pct.toFixed(0) : '—' }}
            </span>
            <span
              class="display-num text-[11px] text-right"
              :class="whiffTone(p.vsL?.whiff_pct)"
            >
              {{ p.vsL?.whiff_pct != null ? p.vsL.whiff_pct.toFixed(0) : '—' }}
            </span>
          </div>
        </div>

        <!-- ─ vs RHB ─ -->
        <div class="bg-bg-50 border border-bg-200">
          <div class="px-3 py-2 border-b border-bg-200 flex items-baseline justify-between">
            <span class="label-bracket text-signal-400">vs RHB</span>
            <span class="label-caps !text-[8px] opacity-70">
              {{ pitchTable.filter(p => p.vsR).length }} pitch{{ pitchTable.filter(p => p.vsR).length === 1 ? '' : 'es' }}
            </span>
          </div>

          <div class="px-3 py-1.5 grid grid-cols-[44px_1fr_38px_38px_38px_38px] gap-1.5 border-b border-bg-200/60">
            <span class="label-caps !text-[8px]">Pitch</span>
            <span class="label-caps !text-[8px]">Usage</span>
            <span class="label-caps !text-[8px] text-right">Velo</span>
            <span class="label-caps !text-[8px] text-right">Brl%</span>
            <span class="label-caps !text-[8px] text-right">HH%</span>
            <span class="label-caps !text-[8px] text-right">Whf%</span>
          </div>

          <div
            v-for="p in pitchTable"
            :key="`r-${p.pitch_type}`"
            class="px-3 py-1.5 grid grid-cols-[44px_1fr_38px_38px_38px_38px] gap-1.5 items-center border-b border-bg-200/40 last:border-0"
          >
            <span class="display-num text-xs text-fg-700 font-medium">{{ p.pitch_type }}</span>

            <div v-if="p.vsR" class="flex items-center gap-2 min-w-0">
              <div class="flex-1 h-1.5 bg-bg-200 relative overflow-hidden">
                <div
                  class="absolute inset-y-0 left-0 bg-signal-400/70"
                  :style="{ width: `${Math.min(p.vsR.usage_pct || 0, 100)}%` }"
                ></div>
              </div>
              <span class="display-num text-[10px] text-fg-500 w-7 text-right">
                {{ p.vsR.usage_pct != null ? p.vsR.usage_pct.toFixed(0) + '%' : '—' }}
              </span>
            </div>
            <span v-else class="text-fg-500 text-[10px] italic">—</span>

            <span class="display-num text-[11px] text-fg-600 text-right">
              {{ p.vsR?.velo_avg != null ? p.vsR.velo_avg.toFixed(0) : '—' }}
            </span>
            <span
              class="display-num text-[11px] text-right"
              :class="statColor(p.vsR?.barrel_pct, 'barrel_pct', 'pitcher')"
            >
              {{ p.vsR?.barrel_pct != null ? p.vsR.barrel_pct.toFixed(1) : '—' }}
            </span>
            <span
              class="display-num text-[11px] text-right"
              :class="statColor(p.vsR?.hard_hit_pct, 'hard_hit_pct', 'pitcher')"
            >
              {{ p.vsR?.hard_hit_pct != null ? p.vsR.hard_hit_pct.toFixed(0) : '—' }}
            </span>
            <span
              class="display-num text-[11px] text-right"
              :class="whiffTone(p.vsR?.whiff_pct)"
            >
              {{ p.vsR?.whiff_pct != null ? p.vsR.whiff_pct.toFixed(0) : '—' }}
            </span>
          </div>
        </div>
      </div>

      <!-- Legend -->
      <div class="mt-3 flex items-center gap-3 flex-wrap text-fg-500">
        <span class="label-caps !text-[8px]">
          Brl% / HH% color · pitcher context (red = elite suppressing)
        </span>
        <span class="label-caps !text-[8px]">
          Whf% · higher = better for pitcher
        </span>
      </div>
    </section>

    <!-- ── ARSENAL empty state ──────────────────────────── -->
    <section v-else class="px-4 sm:px-6 py-4 pb-8">
      <div class="flex items-baseline justify-between mb-3">
        <h2 class="label-bracket text-signal-400">arsenal · vs L / vs R</h2>
      </div>
      <div class="bg-bg-50 border border-bg-200 px-4 py-6 text-center">
        <div class="text-fg-500 text-xs italic">
          No arsenal data recorded for this pitcher yet.
        </div>
      </div>
    </section>

    <!-- ── EMPTY STATE — no pitcher_stats at all ──────── -->
    <section
      v-if="!hasAnyData && !loading"
      class="px-4 sm:px-6 py-10 text-center"
    >
      <div class="display-text text-xl text-fg-500 italic mb-2">
        No pitching data yet
      </div>
      <p class="text-fg-500 text-xs">
        This pitcher has no recorded stats for {{ CURRENT_SEASON }}.
      </p>
    </section>

    <!-- Error -->
    <div v-if="error" class="px-4 sm:px-6 py-4">
      <div class="border border-signal-400/30 bg-signal-400/5 p-4">
        <div class="label-bracket text-signal-400 mb-2">load error</div>
        <div class="text-fg-600 font-mono text-xs">{{ error }}</div>
      </div>
    </div>
  </div>
</template>
