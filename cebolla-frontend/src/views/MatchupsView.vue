<script setup>
/**
 * MatchupsView — Pitcher vs Batter pitch-type matchup analyzer.
 *
 * Surfaces each batter's "danger pitch": the pitch type where their HR rate
 * AND the opposing pitcher's usage % are both high. This is the visible
 * version of what arsenal_adj does internally in compute_projections.
 *
 * v3 (2026-05-21): rebuilt to match the rest of the Cebolla design system —
 * Tailwind utility classes, display-text/display-num/label-caps typography,
 * bg, fg, signal, edge color tokens. Column headers added. Tooltips
 * everywhere. Legend pulled to the top.
 *
 * Danger scoring formula:
 *   danger(pitch) = pitcher_usage_pct(pitch) × batter_hr_rate_vs(pitch)
 *
 * Color tiers (ratio to batter's overall HR rate):
 *   Elite   ≥ 1.5×     → signal red (hottest)
 *   Above   ≥ 1.15×    → edge-hot-1 amber
 *   Average 0.85–1.15× → neutral
 *   Below   < 0.85×    → edge-cold-1 blue
 *   Weak    < 0.6×     → edge-cold-2 darker blue
 *   Small sample (<20 PA vs that pitch) → muted gray
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { teamLogoUrl, playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'
import LoadingBrand from '../components/LoadingBrand.vue'

const loading = ref(true)
const error = ref(null)
const activeDate = ref(null)
const games = ref([])
const lineups = ref({})
const players = ref({})
const arsenals = ref({})
const batterStats = ref({})
const showGuide = ref(false)

const MIN_SAMPLE_PA = 20
const DANGER_TIERS = {
  ELITE:   1.5,
  ABOVE:   1.15,
  AVERAGE: 0.85,
  BELOW:   0.6,
}

const PITCH_NAMES = {
  FF: '4-Seam Fastball',
  SI: 'Sinker',
  FT: '2-Seam Fastball',
  FC: 'Cutter',
  CT: 'Cutter',
  SL: 'Slider',
  ST: 'Sweeper',
  CB: 'Curveball',
  CU: 'Curveball',
  KC: 'Knuckle Curve',
  CH: 'Changeup',
  FS: 'Splitter',
  FO: 'Forkball',
  KN: 'Knuckleball',
  SV: 'Slurve',
  SC: 'Screwball',
  EP: 'Eephus',
}
function pitchName(code) {
  return PITCH_NAMES[code] || code
}

function getETDate(offsetDays = 0) {
  const fmt = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  })
  const d = new Date()
  d.setDate(d.getDate() + offsetDays)
  return fmt.format(d)
}

async function pickActiveDate() {
  const today = getETDate(0)
  const yesterday = getETDate(-1)

  // Status filter syntax matches PlayerView/TeamView/PitcherDeepDive — values
  // with spaces need quotes or PostgREST may parse them as separate tokens.
  const { data: y } = await supabase
    .from('games')
    .select('id')
    .eq('game_date', yesterday)
    .not('status', 'in', '("Final","Game Over","Completed Early")')
    .limit(1)
  if (y && y.length > 0) return yesterday

  const { data: t } = await supabase
    .from('games')
    .select('game_date')
    .gte('game_date', today)
    .not('status', 'in', '("Final","Game Over","Completed Early")')
    .order('game_date', { ascending: true })
    .limit(1)
  if (t && t.length > 0) return t[0].game_date

  return today
}

async function loadGames(date) {
  const { data, error: err } = await supabase
    .from('games')
    .select(`
      id, mlb_game_pk, game_date, game_time_utc, status, venue,
      away_team_id, home_team_id, away_pitcher_id, home_pitcher_id,
      away_team:teams!games_away_team_id_fkey(id, mlb_id, abbrev, name, is_dome),
      home_team:teams!games_home_team_id_fkey(id, mlb_id, abbrev, name, is_dome),
      temp_f, wind_mph, wind_dir_deg, wind_label, precip_pct,
      hr_factor_overall, hr_factor_lhb, hr_factor_rhb
    `)
    .eq('game_date', date)
    .order('game_time_utc', { ascending: true })

  if (err) throw err
  return data || []
}

async function loadLineups(gameIds) {
  if (!gameIds.length) return {}
  const { data, error: err } = await supabase
    .from('lineups')
    .select('game_id, team_id, player_id, batting_order, is_confirmed, bats')
    .in('game_id', gameIds)
    .order('batting_order', { ascending: true })

  if (err) throw err

  const out = {}
  for (const row of data || []) {
    out[row.game_id] = out[row.game_id] || { byTeam: {} }
    out[row.game_id].byTeam[row.team_id] = out[row.game_id].byTeam[row.team_id] || []
    out[row.game_id].byTeam[row.team_id].push(row)
  }
  return out
}

async function loadPlayers(playerIds) {
  if (!playerIds.length) return {}
  const { data, error: err } = await supabase
    .from('players')
    .select('id, mlbam_id, name, bats, throws, team_id')
    .in('id', playerIds)

  if (err) throw err
  const out = {}
  for (const p of data || []) out[p.id] = p
  return out
}

async function loadArsenals(pitcherIds, season) {
  if (!pitcherIds.length) return {}
  const { data, error: err } = await supabase
    .from('pitcher_arsenals')
    .select('pitcher_id, vs_stance, pitch_type, usage_pct, hr_pct, pa, velo_avg')
    .in('pitcher_id', pitcherIds)
    .eq('season', season)
    .eq('window_type', 'season')

  if (err) throw err

  const out = {}
  for (const row of data || []) {
    out[row.pitcher_id] = out[row.pitcher_id] || { L: [], R: [] }
    // Defensive: only push for known stances. The schema constrains vs_stance
    // to 'L' or 'R' by convention but isn't CHECK-enforced. If an ingest bug
    // ever wrote 'A' or 'S' or null, the previous version would crash here
    // with "Cannot read property 'push' of undefined".
    const bucket = out[row.pitcher_id][row.vs_stance]
    if (bucket) bucket.push(row)
  }
  for (const pid of Object.keys(out)) {
    for (const stance of ['L', 'R']) {
      out[pid][stance].sort((a, b) => (b.usage_pct || 0) - (a.usage_pct || 0))
    }
  }
  return out
}

async function loadBatterStats(batterIds, season) {
  if (!batterIds.length) return {}
  const { data, error: err } = await supabase
    .from('batter_stats')
    .select('batter_id, vs_hand, pa, hr, hr_per_pa, by_pitch_type')
    .in('batter_id', batterIds)
    .eq('season', season)
    .eq('window_type', 'season')
    .eq('vs_hand', 'A')

  if (err) throw err
  const out = {}
  for (const row of data || []) out[row.batter_id] = row
  return out
}

function computeDangerPitch(batterStat, pitcherArsenal) {
  if (!batterStat || !pitcherArsenal) return null
  const byPitch = batterStat.by_pitch_type || {}
  const overallHrPct = (batterStat.hr_per_pa || 0) * 100

  let best = null
  for (const pitch of pitcherArsenal) {
    const usage = pitch.usage_pct || 0
    if (usage < 5) continue
    const batterPitchStat = byPitch[pitch.pitch_type]
    if (!batterPitchStat) continue

    const batterHrPct = parseFloat(batterPitchStat.hr_pct) || 0
    const batterPaVsPitch = batterPitchStat.pa || 0
    const pitcherHrAllowedPct = parseFloat(pitch.hr_pct) || 0

    const danger = (usage / 100) * batterHrPct

    if (!best || danger > best.danger_score) {
      best = {
        pitch_type: pitch.pitch_type,
        usage_pct: usage,
        batter_hr_pct: batterHrPct,
        pitcher_hr_allowed_pct: pitcherHrAllowedPct,
        danger_score: danger,
        batter_pa_vs_pitch: batterPaVsPitch,
        small_sample: batterPaVsPitch < MIN_SAMPLE_PA,
        ratio_to_overall: overallHrPct > 0 ? batterHrPct / overallHrPct : 1.0,
      }
    }
  }
  return best
}

function colorTier(ratio, smallSample) {
  if (smallSample) return 'sample'
  if (ratio >= DANGER_TIERS.ELITE) return 'elite'
  if (ratio >= DANGER_TIERS.ABOVE) return 'above'
  if (ratio < DANGER_TIERS.BELOW) return 'weak'
  if (ratio < DANGER_TIERS.AVERAGE) return 'below'
  return 'avg'
}

const TIER_CLASS = {
  elite:  'bg-signal-400/20 text-signal-200 border border-signal-400/40',
  above:  'bg-edge-hot-1/15 text-edge-hot-1 border border-edge-hot-1/35',
  avg:    'bg-bg-150 text-fg-600 border border-bg-200',
  below:  'bg-edge-cold-1/12 text-edge-cold-1 border border-edge-cold-1/30',
  weak:   'bg-edge-cold-2/18 text-edge-cold-1 border border-edge-cold-2/40',
  sample: 'bg-bg-100 text-fg-400 border border-bg-200',
}

const TIER_LABEL = {
  elite:  "Elite matchup \u2014 \u22651.5\u00d7 this batter's normal HR rate",
  above:  "Above average \u2014 1.15\u20131.5\u00d7 this batter's normal HR rate",
  avg:    "Average \u2014 within \u00b115% of this batter's normal HR rate",
  below:  "Below average \u2014 0.6\u20130.85\u00d7 this batter's normal HR rate",
  weak:   "Weak matchup \u2014 <0.6\u00d7 this batter's normal HR rate",
  sample: "Small sample \u2014 fewer than 20 PA vs this pitch type",
}

const matchupGames = computed(() => {
  if (!games.value.length) return []

  return games.value.map(g => {
    const gameId = g.id
    const gameLineups = lineups.value[gameId]?.byTeam || {}
    const awayLineupRows = gameLineups[g.away_team_id] || []
    const homeLineupRows = gameLineups[g.home_team_id] || []

    function buildBatterRows(lineupRows, opposingPitcherId) {
      const pitcherArsenalAll = arsenals.value[opposingPitcherId] || { L: [], R: [] }
      const pitcher = players.value[opposingPitcherId]
      const pitcherThrows = pitcher?.throws || 'R'

      return lineupRows.slice(0, 9).map(row => {
        const batter = players.value[row.player_id]
        if (!batter) return null
        // Never default unknown handedness to 'R' — a null players.bats (data
        // gap, ~72% of position players as of 2026-05) would otherwise display
        // as right-handed and actively mislead. Null flows through to the
        // template, which renders "—" instead of a fake hand.
        // NOTE: effectiveStance below still falls back to 'R' for the danger-
        // pitch math when handedness is unknown; that's a separate concern the
        // bats backfill resolves. This change is display-only.
        const batSide = batter.bats || row.bats || null
        const effectiveStance = batSide === 'S'
          ? (pitcherThrows === 'R' ? 'L' : 'R')
          : (batSide === 'L' ? 'L' : 'R')
        const arsenal = pitcherArsenalAll[effectiveStance] || []
        const stat = batterStats.value[row.player_id]
        const danger = computeDangerPitch(stat, arsenal)
        return {
          batting_order: row.batting_order,
          is_confirmed: row.is_confirmed,
          player_id: row.player_id,
          mlbam_id: batter.mlbam_id,
          name: batter.name,
          bats: batSide,
          effective_stance: effectiveStance,
          season_hr_pct: stat ? (stat.hr_per_pa || 0) * 100 : null,
          season_pa: stat?.pa || 0,
          danger,
        }
      }).filter(Boolean)
    }

    function topPitches(pitcherId) {
      const a = arsenals.value[pitcherId]
      if (!a) return []
      const combined = {}
      for (const stance of ['L', 'R']) {
        for (const p of a[stance] || []) {
          combined[p.pitch_type] = combined[p.pitch_type] || {
            pitch_type: p.pitch_type, usage_pct: 0, samples: 0,
          }
          combined[p.pitch_type].usage_pct += (p.usage_pct || 0)
          combined[p.pitch_type].samples += 1
        }
      }
      return Object.values(combined)
        .map(p => ({ ...p, usage_pct: p.samples ? p.usage_pct / p.samples : 0 }))
        .sort((a, b) => b.usage_pct - a.usage_pct)
        .slice(0, 4)
    }

    const awayPitcher = players.value[g.away_pitcher_id]
    const homePitcher = players.value[g.home_pitcher_id]

    // Arsenal availability flags. A pitcher has no usable arsenal data when
    // pitcher_arsenals returned zero rows (rookies, recent call-ups, pitchers
    // returning from IL who haven't accumulated enough Statcast pitches yet).
    // Without this data, danger-pitch computation can't run for any batter
    // facing them. We surface that explicitly in the UI instead of showing a
    // wall of dashes with no explanation.
    const awayHasArsenal = !!(arsenals.value[g.away_pitcher_id]
      && ((arsenals.value[g.away_pitcher_id].L || []).length
        || (arsenals.value[g.away_pitcher_id].R || []).length))
    const homeHasArsenal = !!(arsenals.value[g.home_pitcher_id]
      && ((arsenals.value[g.home_pitcher_id].L || []).length
        || (arsenals.value[g.home_pitcher_id].R || []).length))

    return {
      id: gameId,
      mlb_game_pk: g.mlb_game_pk,
      away_team: g.away_team,
      home_team: g.home_team,
      away_team_id: g.away_team_id,
      home_team_id: g.home_team_id,
      game_time_utc: g.game_time_utc,
      status: g.status,
      venue: g.venue,
      temp_f: g.temp_f,
      wind_mph: g.wind_mph,
      wind_label: g.wind_label,
      hr_factor: g.hr_factor_overall,
      is_dome: g.home_team?.is_dome || g.wind_label === 'dome',

      away_pitcher: awayPitcher,
      home_pitcher: homePitcher,
      away_pitcher_top: topPitches(g.away_pitcher_id),
      home_pitcher_top: topPitches(g.home_pitcher_id),
      away_pitcher_has_arsenal: awayHasArsenal,
      home_pitcher_has_arsenal: homeHasArsenal,

      home_lineup: buildBatterRows(homeLineupRows, g.away_pitcher_id),
      away_lineup: buildBatterRows(awayLineupRows, g.home_pitcher_id),

      home_confirmed: homeLineupRows.length > 0 && homeLineupRows.every(r => r.is_confirmed),
      away_confirmed: awayLineupRows.length > 0 && awayLineupRows.every(r => r.is_confirmed),
    }
  })
})

function fmtTime(utcStr) {
  if (!utcStr) return ''
  return new Date(utcStr).toLocaleTimeString('en-US', {
    hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York',
  })
}

function fmtHrFactor(f) {
  if (f == null) return ''
  return Number(f).toFixed(2)
}

const dateLong = computed(() => {
  if (!activeDate.value) return ''
  const [y, m, d] = activeDate.value.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  return dt.toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  })
})

function teamLogo(team) {
  return team?.mlb_id ? teamLogoUrl(team.mlb_id) : null
}
function headshot(mlbamId, size = 40) {
  return mlbamId ? playerHeadshotUrl(mlbamId, { size }) : null
}

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    const date = await pickActiveDate()
    activeDate.value = date
    // Parse year directly from the YYYY-MM-DD string. Using `new Date(date)`
    // would interpret as UTC midnight, then `.getFullYear()` would use the
    // user's LOCAL TZ — for a West Coast user, the date "2026-01-01" would
    // parse to "2025-12-31 16:00 PT" and `.getFullYear()` would return 2025.
    // No real impact for MLB (no Jan 1 games) but defensive coding for the
    // year-boundary edge case.
    const season = parseInt(date.slice(0, 4), 10)

    const gamesData = await loadGames(date)
    games.value = gamesData

    if (!gamesData.length) {
      loading.value = false
      return
    }

    const gameIds = gamesData.map(g => g.id)
    const pitcherIds = []
    for (const g of gamesData) {
      if (g.away_pitcher_id) pitcherIds.push(g.away_pitcher_id)
      if (g.home_pitcher_id) pitcherIds.push(g.home_pitcher_id)
    }

    const lineupsData = await loadLineups(gameIds)
    lineups.value = lineupsData

    const batterIds = []
    for (const gid of Object.keys(lineupsData)) {
      for (const tid of Object.keys(lineupsData[gid].byTeam)) {
        for (const row of lineupsData[gid].byTeam[tid]) {
          if (row.player_id) batterIds.push(row.player_id)
        }
      }
    }

    const allPlayerIds = [...new Set([...pitcherIds, ...batterIds])]
    const [playersData, arsenalsData, batterStatsData] = await Promise.all([
      loadPlayers(allPlayerIds),
      loadArsenals(pitcherIds, season),
      loadBatterStats(batterIds, season),
    ])

    players.value = playersData
    arsenals.value = arsenalsData
    batterStats.value = batterStatsData
  } catch (e) {
    console.error('[MatchupsView] load failed:', e)
    error.value = e.message || 'Failed to load matchups'
  } finally {
    loading.value = false
  }
}

// Realtime: re-load when lineups or games change. Lineups especially —
// they get posted by MLB ~2-3 hours before first pitch and we want the
// page to reflect "confirmed" (vs "predicted") status without a manual
// refresh. Debounced because a single lineup post writes 9-13 rows in
// a quick burst.
let channel = null
let reloadTimer = null
function scheduleReload() {
  if (reloadTimer) clearTimeout(reloadTimer)
  reloadTimer = setTimeout(() => loadAll(), 3000)
}

onMounted(() => {
  loadAll()
  channel = supabase
    .channel('matchups-changes')
    .on('postgres_changes',
        { event: '*', schema: 'public', table: 'lineups' },
        () => scheduleReload())
    .on('postgres_changes',
        { event: 'UPDATE', schema: 'public', table: 'games' },
        () => scheduleReload())
    .subscribe()
})

onUnmounted(() => {
  if (channel) supabase.removeChannel(channel)
  if (reloadTimer) clearTimeout(reloadTimer)
})
</script>

<template>
  <div class="min-h-screen">

    <section class="px-4 sm:px-6 pt-6 pb-4">
      <div class="flex items-baseline gap-3 mb-2">
        <h1 class="display-text text-2xl sm:text-3xl text-fg-800">Matchups</h1>
        <span class="label-bracket text-fg-500">M.08</span>
      </div>
      <p class="text-fg-500 text-sm max-w-2xl">
        Pitcher arsenals crossed with batter pitch-type vulnerabilities. For each batter facing
        each pitcher, we find the one pitch type where the pitcher's usage and the batter's HR
        rate combine most dangerously &mdash; and color the result against that batter's own
        season HR rate.
      </p>
    </section>

    <section v-if="!loading && games.length" class="px-4 sm:px-6 mb-4">
      <div class="rounded-md border border-bg-200 bg-bg-50 p-3">
        <div class="flex items-center justify-between gap-3 flex-wrap mb-2">
          <span class="label-caps">Legend</span>
          <button
            type="button"
            @click="showGuide = !showGuide"
            class="label-caps !text-[10px] text-fg-500 hover:text-signal-300 transition flex items-center gap-1 min-h-[36px] -my-1.5"
          >
            <span>{{ showGuide ? 'Hide' : 'How to read this' }}</span>
            <span>{{ showGuide ? '\u2212' : '+' }}</span>
          </button>
        </div>
        <div class="flex flex-wrap gap-2 text-[11px] font-mono">
          <span class="px-2 py-0.5 rounded-sm border bg-signal-400/20 text-signal-200 border-signal-400/40">Elite &ge;1.5&times;</span>
          <span class="px-2 py-0.5 rounded-sm border bg-edge-hot-1/15 text-edge-hot-1 border-edge-hot-1/35">Above &ge;1.15&times;</span>
          <span class="px-2 py-0.5 rounded-sm border bg-bg-150 text-fg-600 border-bg-200">Average</span>
          <span class="px-2 py-0.5 rounded-sm border bg-edge-cold-1/12 text-edge-cold-1 border-edge-cold-1/30">Below &le;0.85&times;</span>
          <span class="px-2 py-0.5 rounded-sm border bg-edge-cold-2/18 text-edge-cold-1 border-edge-cold-2/40">Weak &le;0.6&times;</span>
          <span class="px-2 py-0.5 rounded-sm border bg-bg-100 text-fg-400 border-bg-200">Small sample (n&lt;20)</span>
        </div>

        <div v-if="showGuide" class="mt-3 pt-3 border-t border-bg-200 text-[12px] text-fg-500 leading-relaxed space-y-2">
          <p>
            <span class="label-caps !text-[10px] text-fg-700">Danger Pitch</span> &mdash;
            the single pitch type where this batter is most likely to do damage against this
            pitcher. Computed as <span class="text-fg-700 font-mono">pitcher_usage &times; batter_hr_rate</span>.
            A pitch that's thrown often AND that this batter punishes is the most dangerous overlap.
          </p>
          <p>
            <span class="label-caps !text-[10px] text-fg-700">HR vs</span> &mdash;
            this batter's HR rate (per plate appearance) when seeing that exact pitch type, across all pitchers
            this season. Color shows how it compares to their overall HR rate. A 4.2% HR rate vs sinkers is
            <span class="text-signal-200">elite</span> for a batter whose season HR/PA is 2.0% (2.1&times;) &mdash; but
            <span class="text-edge-cold-1">below average</span> for a slugger whose overall HR/PA is 6.0% (0.7&times;).
          </p>
          <p>
            <span class="label-caps !text-[10px] text-fg-700">Pitch codes</span> &mdash;
            hover any code to see the pitch name (FF = 4-Seam, SI = Sinker, SL = Slider, CT = Cutter, CB = Curveball, CH = Changeup, FS = Splitter, etc.)
          </p>
          <p class="text-fg-400">
            Small-sample rows (fewer than 20 PA vs that pitch type) are grayed out &mdash; those HR rates are
            noisy and shouldn't be trusted in isolation.
          </p>
        </div>
      </div>
    </section>

    <LoadingBrand v-if="loading" text="Loading matchups…" />

    <div v-else-if="error" class="px-6 py-12 text-edge-cold-1 text-sm">
      Couldn't load matchups: {{ error }}
      <button @click="loadAll" class="ml-3 underline text-fg-500 hover:text-signal-300">Try again</button>
    </div>

    <div v-else-if="!games.length" class="px-6 py-12 text-fg-500 text-sm">
      No games scheduled for {{ dateLong }}.
    </div>

    <section v-else class="px-4 sm:px-6 pb-12">
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">

        <article
          v-for="game in matchupGames"
          :key="game.id"
          class="rounded-lg border border-bg-200 bg-bg-50 overflow-hidden flex flex-col"
        >

          <div class="px-3 py-2 border-b border-bg-200 flex items-center justify-between gap-2">
            <div class="flex items-center gap-2 min-w-0">
              <img v-if="teamLogo(game.away_team)" :src="teamLogo(game.away_team)" :alt="game.away_team?.abbrev" class="w-5 h-5 object-contain shrink-0 opacity-95" loading="lazy" @error="hideOnError" />
              <span class="display-text text-sm text-fg-700">{{ game.away_team?.abbrev }}</span>
              <span class="text-fg-500 text-xs">@</span>
              <span class="display-text text-sm text-fg-700">{{ game.home_team?.abbrev }}</span>
              <img v-if="teamLogo(game.home_team)" :src="teamLogo(game.home_team)" :alt="game.home_team?.abbrev" class="w-5 h-5 object-contain shrink-0 opacity-95" loading="lazy" @error="hideOnError" />
            </div>
            <span class="display-num text-[11px] text-fg-500">{{ fmtTime(game.game_time_utc) }} ET</span>
          </div>

          <div class="px-3 pt-2 flex gap-1.5 flex-wrap">
            <span v-if="game.is_dome" class="text-[9px] font-mono uppercase tracking-wide px-1.5 py-0.5 rounded-sm bg-bg-150 text-fg-500" title="Indoor stadium &mdash; weather doesn't affect this game">Dome</span>
            <span v-else-if="game.wind_mph" class="text-[9px] font-mono uppercase tracking-wide px-1.5 py-0.5 rounded-sm bg-edge-cold-1/12 text-edge-cold-1" :title="`Wind: ${Math.round(game.wind_mph)} mph, direction ${game.wind_label || 'unknown'}`">{{ Math.round(game.wind_mph) }}mph {{ game.wind_label }}</span>
            <span v-if="game.temp_f && !game.is_dome" class="text-[9px] font-mono uppercase tracking-wide px-1.5 py-0.5 rounded-sm bg-bg-150 text-fg-500" :title="`Game-time temperature: ${Math.round(game.temp_f)}\u00b0F`">{{ Math.round(game.temp_f) }}&deg;F</span>
            <span v-if="game.hr_factor" class="text-[9px] font-mono uppercase tracking-wide px-1.5 py-0.5 rounded-sm" :class="game.hr_factor >= 1.15 ? 'bg-signal-400/15 text-signal-300' : game.hr_factor <= 0.9 ? 'bg-edge-cold-1/12 text-edge-cold-1' : 'bg-bg-150 text-fg-500'" :title="`HR park factor \u2014 1.00 is league average. Higher values mean HRs are more common at this venue.`">HR {{ fmtHrFactor(game.hr_factor) }}</span>
          </div>

          <div class="px-3 pt-3 pb-2">

            <div class="flex gap-2.5 items-start">
              <img v-if="headshot(game.away_pitcher?.mlbam_id, 60)" :src="headshot(game.away_pitcher?.mlbam_id, 60)" :alt="game.away_pitcher?.name" class="w-11 h-11 rounded-full object-cover bg-bg-150 shrink-0" loading="lazy" @error="hideOnError" />
              <div v-else class="w-11 h-11 rounded-full bg-bg-150 shrink-0"></div>
              <div class="min-w-0 flex-1">
                <div class="label-caps !text-[9px]">Away SP</div>
                <router-link v-if="game.away_pitcher?.id" :to="`/player/${game.away_pitcher.id}`" class="text-fg-700 hover:text-signal-300 transition text-sm font-medium block truncate">
                  {{ game.away_pitcher?.name }}
                  <span v-if="game.away_pitcher?.throws" class="display-num text-[10px] text-fg-500 ml-1" :title="`Throws ${game.away_pitcher.throws === 'L' ? 'left' : 'right'}-handed`">{{ game.away_pitcher.throws }}HP</span>
                </router-link>
                <span v-else class="text-fg-500 text-sm">TBD</span>
                <div class="flex gap-1 flex-wrap mt-1">
                  <span v-for="(p, i) in game.away_pitcher_top" :key="p.pitch_type" class="display-num text-[10px] px-1.5 py-0.5 rounded-sm" :class="i < 3 ? 'bg-bg-150 text-fg-600' : 'bg-bg-100 text-fg-400'" :title="`${pitchName(p.pitch_type)} \u2014 thrown ${Math.round(p.usage_pct)}% of the time this season`">{{ p.pitch_type }} {{ Math.round(p.usage_pct) }}%</span>
                </div>
              </div>
            </div>

            <div class="mt-3 mb-1.5 flex items-center gap-1.5">
              <img v-if="teamLogo(game.home_team)" :src="teamLogo(game.home_team)" :alt="game.home_team?.abbrev" class="w-3.5 h-3.5 object-contain shrink-0 opacity-80" loading="lazy" @error="hideOnError" />
              <span class="label-caps">{{ game.home_team?.abbrev }} batting</span>
              <span v-if="!game.home_confirmed && game.home_lineup.length" class="text-[9px] font-mono uppercase tracking-wide font-medium px-2 py-0.5 rounded-sm bg-edge-hot-1/20 text-edge-hot-1 border border-edge-hot-1/30 ml-auto" title="Lineup is predicted from recent games &mdash; not yet officially posted by MLB">predicted</span>
            </div>

            <div v-if="game.home_lineup.length && game.away_pitcher_has_arsenal">
              <div class="grid grid-cols-[14px_24px_1fr_auto_60px] gap-2 items-center pb-1 border-b border-bg-200 mb-1">
                <span></span>
                <span></span>
                <span class="label-caps !text-[9px]">Batter</span>
                <span class="label-caps !text-[9px]" title="The most dangerous pitch type in this matchup">Danger Pitch</span>
                <span class="label-caps !text-[9px] text-right" title="Batter's HR rate vs that pitch type &mdash; colored vs their own season HR rate">HR vs</span>
              </div>
              <div v-for="b in game.home_lineup" :key="b.player_id" class="grid grid-cols-[14px_24px_1fr_auto_60px] gap-2 items-center py-1 text-[11px] hover:bg-bg-100/40 transition rounded-sm">
                <span class="display-num text-[10px] text-fg-400 text-center">{{ b.batting_order }}</span>
                <img v-if="headshot(b.mlbam_id, 40)" :src="headshot(b.mlbam_id, 40)" :alt="b.name" class="w-5 h-5 rounded-full object-cover bg-bg-150" loading="lazy" @error="hideOnError" />
                <div v-else class="w-5 h-5 rounded-full bg-bg-150"></div>
                <router-link :to="`/player/${b.player_id}`" class="text-fg-700 hover:text-signal-300 transition truncate min-w-0" :title="b.bats ? `${b.name} \u2014 bats ${b.bats === 'L' ? 'left' : b.bats === 'S' ? 'switch (effectively ' + b.effective_stance + ' vs this pitcher)' : 'right'}` : `${b.name} \u2014 batting handedness unknown`">
                  {{ b.name }}<span v-if="b.bats" class="text-fg-400 text-[9px] ml-1 display-num">{{ b.bats }}</span><span v-else class="text-fg-400/40 text-[9px] ml-1 display-num" title="Batting handedness unknown">&mdash;</span>
                </router-link>
                <span v-if="b.danger" class="display-num text-[10px] px-1.5 py-0.5 rounded-sm bg-bg-150 text-fg-600 whitespace-nowrap" :title="`${pitchName(b.danger.pitch_type)} \u2014 pitcher throws ${Math.round(b.danger.usage_pct)}% of the time`">{{ b.danger.pitch_type }} <span class="text-fg-400">{{ Math.round(b.danger.usage_pct) }}%</span></span>
                <span v-else class="text-fg-400 text-[10px] text-right">&mdash;</span>
                <span v-if="b.danger" class="display-num text-[10px] px-1.5 py-0.5 rounded-sm text-right whitespace-nowrap" :class="TIER_CLASS[colorTier(b.danger.ratio_to_overall, b.danger.small_sample)]" :title="`${TIER_LABEL[colorTier(b.danger.ratio_to_overall, b.danger.small_sample)]}\nSample: ${b.danger.batter_pa_vs_pitch} PA vs ${b.danger.pitch_type} this season\nBatter season HR/PA: ${b.season_hr_pct?.toFixed(1)}%`">{{ b.danger.batter_hr_pct.toFixed(1) }}%</span>
                <span v-else class="text-fg-400 text-[10px] text-right">&mdash;</span>
              </div>
            </div>
            <div v-else-if="game.home_lineup.length && !game.away_pitcher_has_arsenal" class="rounded-sm border border-bg-200 bg-bg-100/50 p-3 mt-1">
              <div class="flex items-start gap-2 mb-2">
                <span class="text-edge-hot-1 text-[12px] leading-none mt-0.5">&#9888;</span>
                <div class="text-[11px] text-fg-500 leading-snug">
                  <span class="text-fg-700">Arsenal data unavailable</span> for <span class="text-fg-600">{{ game.away_pitcher?.name || 'this pitcher' }}</span>. Matchup math needs Statcast pitch tracking (typically ~50+ pitches this season). Likely a recent call-up or returning from injury.
                </div>
              </div>
              <div class="text-[10px] text-fg-400 mt-2 pt-2 border-t border-bg-200">
                {{ game.home_team?.abbrev }} batting order: <span class="display-num text-fg-500">{{ game.home_lineup.map(b => b.name).join(' &middot; ') }}</span>
              </div>
            </div>
            <p v-else class="text-fg-500 text-[11px] italic py-2">Lineup not yet posted.</p>
          </div>

          <div class="h-px bg-bg-200 mx-3"></div>

          <div class="px-3 pt-3 pb-3">

            <div class="flex gap-2.5 items-start">
              <img v-if="headshot(game.home_pitcher?.mlbam_id, 60)" :src="headshot(game.home_pitcher?.mlbam_id, 60)" :alt="game.home_pitcher?.name" class="w-11 h-11 rounded-full object-cover bg-bg-150 shrink-0" loading="lazy" @error="hideOnError" />
              <div v-else class="w-11 h-11 rounded-full bg-bg-150 shrink-0"></div>
              <div class="min-w-0 flex-1">
                <div class="label-caps !text-[9px]">Home SP</div>
                <router-link v-if="game.home_pitcher?.id" :to="`/player/${game.home_pitcher.id}`" class="text-fg-700 hover:text-signal-300 transition text-sm font-medium block truncate">
                  {{ game.home_pitcher?.name }}
                  <span v-if="game.home_pitcher?.throws" class="display-num text-[10px] text-fg-500 ml-1" :title="`Throws ${game.home_pitcher.throws === 'L' ? 'left' : 'right'}-handed`">{{ game.home_pitcher.throws }}HP</span>
                </router-link>
                <span v-else class="text-fg-500 text-sm">TBD</span>
                <div class="flex gap-1 flex-wrap mt-1">
                  <span v-for="(p, i) in game.home_pitcher_top" :key="p.pitch_type" class="display-num text-[10px] px-1.5 py-0.5 rounded-sm" :class="i < 3 ? 'bg-bg-150 text-fg-600' : 'bg-bg-100 text-fg-400'" :title="`${pitchName(p.pitch_type)} \u2014 thrown ${Math.round(p.usage_pct)}% of the time this season`">{{ p.pitch_type }} {{ Math.round(p.usage_pct) }}%</span>
                </div>
              </div>
            </div>

            <div class="mt-3 mb-1.5 flex items-center gap-1.5">
              <img v-if="teamLogo(game.away_team)" :src="teamLogo(game.away_team)" :alt="game.away_team?.abbrev" class="w-3.5 h-3.5 object-contain shrink-0 opacity-80" loading="lazy" @error="hideOnError" />
              <span class="label-caps">{{ game.away_team?.abbrev }} batting</span>
              <span v-if="!game.away_confirmed && game.away_lineup.length" class="text-[9px] font-mono uppercase tracking-wide font-medium px-2 py-0.5 rounded-sm bg-edge-hot-1/20 text-edge-hot-1 border border-edge-hot-1/30 ml-auto" title="Lineup is predicted from recent games &mdash; not yet officially posted by MLB">predicted</span>
            </div>

            <div v-if="game.away_lineup.length && game.home_pitcher_has_arsenal">
              <div class="grid grid-cols-[14px_24px_1fr_auto_60px] gap-2 items-center pb-1 border-b border-bg-200 mb-1">
                <span></span>
                <span></span>
                <span class="label-caps !text-[9px]">Batter</span>
                <span class="label-caps !text-[9px]" title="The most dangerous pitch type in this matchup">Danger Pitch</span>
                <span class="label-caps !text-[9px] text-right" title="Batter's HR rate vs that pitch type &mdash; colored vs their own season HR rate">HR vs</span>
              </div>
              <div v-for="b in game.away_lineup" :key="b.player_id" class="grid grid-cols-[14px_24px_1fr_auto_60px] gap-2 items-center py-1 text-[11px] hover:bg-bg-100/40 transition rounded-sm">
                <span class="display-num text-[10px] text-fg-400 text-center">{{ b.batting_order }}</span>
                <img v-if="headshot(b.mlbam_id, 40)" :src="headshot(b.mlbam_id, 40)" :alt="b.name" class="w-5 h-5 rounded-full object-cover bg-bg-150" loading="lazy" @error="hideOnError" />
                <div v-else class="w-5 h-5 rounded-full bg-bg-150"></div>
                <router-link :to="`/player/${b.player_id}`" class="text-fg-700 hover:text-signal-300 transition truncate min-w-0" :title="b.bats ? `${b.name} \u2014 bats ${b.bats === 'L' ? 'left' : b.bats === 'S' ? 'switch (effectively ' + b.effective_stance + ' vs this pitcher)' : 'right'}` : `${b.name} \u2014 batting handedness unknown`">
                  {{ b.name }}<span v-if="b.bats" class="text-fg-400 text-[9px] ml-1 display-num">{{ b.bats }}</span><span v-else class="text-fg-400/40 text-[9px] ml-1 display-num" title="Batting handedness unknown">&mdash;</span>
                </router-link>
                <span v-if="b.danger" class="display-num text-[10px] px-1.5 py-0.5 rounded-sm bg-bg-150 text-fg-600 whitespace-nowrap" :title="`${pitchName(b.danger.pitch_type)} \u2014 pitcher throws ${Math.round(b.danger.usage_pct)}% of the time`">{{ b.danger.pitch_type }} <span class="text-fg-400">{{ Math.round(b.danger.usage_pct) }}%</span></span>
                <span v-else class="text-fg-400 text-[10px] text-right">&mdash;</span>
                <span v-if="b.danger" class="display-num text-[10px] px-1.5 py-0.5 rounded-sm text-right whitespace-nowrap" :class="TIER_CLASS[colorTier(b.danger.ratio_to_overall, b.danger.small_sample)]" :title="`${TIER_LABEL[colorTier(b.danger.ratio_to_overall, b.danger.small_sample)]}\nSample: ${b.danger.batter_pa_vs_pitch} PA vs ${b.danger.pitch_type} this season\nBatter season HR/PA: ${b.season_hr_pct?.toFixed(1)}%`">{{ b.danger.batter_hr_pct.toFixed(1) }}%</span>
                <span v-else class="text-fg-400 text-[10px] text-right">&mdash;</span>
              </div>
            </div>
            <div v-else-if="game.away_lineup.length && !game.home_pitcher_has_arsenal" class="rounded-sm border border-bg-200 bg-bg-100/50 p-3 mt-1">
              <div class="flex items-start gap-2 mb-2">
                <span class="text-edge-hot-1 text-[12px] leading-none mt-0.5">&#9888;</span>
                <div class="text-[11px] text-fg-500 leading-snug">
                  <span class="text-fg-700">Arsenal data unavailable</span> for <span class="text-fg-600">{{ game.home_pitcher?.name || 'this pitcher' }}</span>. Matchup math needs Statcast pitch tracking (typically ~50+ pitches this season). Likely a recent call-up or returning from injury.
                </div>
              </div>
              <div class="text-[10px] text-fg-400 mt-2 pt-2 border-t border-bg-200">
                {{ game.away_team?.abbrev }} batting order: <span class="display-num text-fg-500">{{ game.away_lineup.map(b => b.name).join(' &middot; ') }}</span>
              </div>
            </div>
            <p v-else class="text-fg-500 text-[11px] italic py-2">Lineup not yet posted.</p>
          </div>

        </article>
      </div>
    </section>
  </div>
</template>
