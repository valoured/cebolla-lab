// useTrends.js — Cebolla Trends/Streaks data layer
//
// "Streaks Lite" — built against the existing batter_stats data we already
// pull nightly (season + l14 windows, vs_hand='A'). When the future
// batter_game_log table lands, we can drop in a v2 that does true L5/L10/
// L15/L20 ring charts. For now: season vs L14 divergence as the primary
// signal, which is honest about what we know.
//
// Returns rows in this shape:
//   {
//     batter:        { id, mlbam_id, name, bats, team_id, team_abbrev, team_mlb_id },
//     pa_l14, pa_season,
//     metric_l14, metric_season, metric_delta, trend_score,
//     hits_l14, hr_l14, ...,
//     playing_today: boolean,
//     today_opponent: { team_abbrev, pitcher_name, pitcher_throws } | null,
//   }
//
// The component is responsible for sorting + presentation. We do the
// joining and the math here so the view stays declarative.

import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'

// Min L14 PA to qualify — below this we don't trust the rate
const DEFAULT_MIN_PA = 20

// Currently-supported metric keys.
//
// `combined` is special — it's not a single column in batter_stats. It's a
// geometric-mean composite of trend scores from the four base metrics
// (hr, hits, barrel, iso). The row builder detects this and computes it
// after the per-metric pass.
export const TREND_METRICS = ['combined', 'hr', 'hits', 'barrel', 'iso']

// Metrics that have a direct extractor. Combined is computed from these.
const BASE_METRICS = ['hr', 'hits', 'barrel', 'iso']

export const METRIC_LABELS = {
  combined: { short: 'Combined', long: 'Combined Heat', unit: '' },
  hr:       { short: 'HR/PA',    long: 'Home Run Rate', unit: '%' },
  hits:     { short: 'H/PA',     long: 'Hit Rate',      unit: '%' },
  barrel:   { short: 'Barrel%',  long: 'Barrel Rate',   unit: '%' },
  iso:      { short: 'ISO',      long: 'Isolated Power', unit: '' },
}

// Each extractor returns the raw metric value (decimal where possible)
// from a single batter_stats row. Returns null when the row lacks the
// data needed.
const METRIC_EXTRACTORS = {
  hr:      (row) => (row?.hr_per_pa != null ? Number(row.hr_per_pa) : null),
  hits:    (row) => (row?.hit_per_pa != null ? Number(row.hit_per_pa) : null),
  // barrel_pct + hard_hit_pct in batter_stats are stored as percent (e.g. 12.3
  // not 0.123). Divide by 100 to keep all metric values on the same 0–1 scale.
  barrel:  (row) => (row?.barrel_pct != null ? Number(row.barrel_pct) / 100 : null),
  iso:     (row) => (row?.iso != null ? Number(row.iso) : null),
}

/**
 * Trend score: relative divergence of L14 from season.
 *   ts > 0  → hotter than season (positive divergence)
 *   ts < 0  → colder than season (negative divergence)
 *
 * When season is essentially zero we return null. This is honest — there's
 * no real baseline to compute relative divergence against, so showing any
 * percentage would be misleading. Rows with null trend_score are skipped
 * by the row builder in `rows.value`.
 *
 * In practice this only fires for:
 *   - Batters who haven't hit a single HR all year (HR/PA only)
 *   - Players with near-zero barrel% or ISO season totals
 * Both are rare and mostly filter out via the minPA gate anyway.
 */
function computeTrendScore(l14, season) {
  if (l14 == null || season == null) return null
  if (season < 0.0001) return null
  return (l14 - season) / season
}

/**
 * Combined Heat — geometric mean of (1 + clamped trend scores) across the
 * base metrics, minus 1. Returns a "% above baseline" figure on the same
 * scale as individual trend scores so tier thresholds stay consistent.
 *
 * Why geometric not arithmetic:
 *   - A player hot on HR but flat on barrel% shouldn't score the same as
 *     one hot on all 4 — geometric punishes disagreement between signals
 *   - Multi-signal agreement produces a more honest "combined heat" read
 *
 * Why clamp the inputs:
 *   - A +500% spike on one metric (small sample) shouldn't drag the whole
 *     combined that high — cap at +200%
 *   - A -100% would zero out the multiplication — floor at -75%
 *
 * Drops null contributors gracefully (some players have no barrel/ISO
 * data). Requires at least 3 of the 4 base metrics to produce a result,
 * otherwise returns null. Two-signal results can be misleadingly high —
 * if a player has L14 data on only HR and hits, they get the same +200%
 * combined as a player with all 4 metrics agreeing at +200%, which
 * isn't fair. Requiring 3+ rules out the sparse-data outliers without
 * filtering out the typical case (most qualifying batters have all 4).
 */
function computeCombinedTrend(perMetricScores) {
  const valid = []
  for (const m of BASE_METRICS) {
    const ts = perMetricScores[m]
    if (ts == null || !Number.isFinite(ts)) continue
    const clamped = Math.max(-0.75, Math.min(2.0, ts))
    valid.push(1 + clamped)
  }
  if (valid.length < 3) return null

  // Geometric mean. With clamps in place, every factor is in [0.25, 3.0]
  // — safe for log/exp, but we'll use log-space anyway for numerical
  // stability if more metrics get added later.
  const logSum = valid.reduce((acc, x) => acc + Math.log(x), 0)
  const geoMean = Math.exp(logSum / valid.length)
  return geoMean - 1
}

export function useTrends() {
  const loading = ref(true)
  const error = ref(null)
  const rawSeasonRows = ref([])
  const rawL14Rows = ref([])
  const playingTodayIds = ref(new Set())
  const todayMatchupByBatter = ref(new Map())

  // UI state — bindable from the view
  const metric = ref('combined')  // 'combined' | 'hr' | 'hits' | 'barrel' | 'iso'
  const direction = ref('hot')    // 'hot' | 'cold'
  const playingTodayOnly = ref(true)
  const minPA = ref(DEFAULT_MIN_PA)

  async function loadStats() {
    loading.value = true
    error.value = null
    try {
      // Season + L14 batter_stats rows. We only want vs_hand='A' (overall)
      // for the headline trend numbers. Handedness splits can come later
      // as a drill-down.
      //
      // We query both windows in parallel and zip them in JS by batter_id.
      const [seasonRes, l14Res] = await Promise.all([
        supabase
          .from('batter_stats')
          .select(`
            batter_id, pa, ab, hits, hr,
            avg, obp, slg, iso,
            hr_per_pa, hit_per_pa,
            barrel_pct, hard_hit_pct, ev_avg, la_avg,
            window_type, vs_hand
          `)
          .eq('window_type', 'season')
          .eq('vs_hand', 'A'),
        supabase
          .from('batter_stats')
          .select(`
            batter_id, pa, ab, hits, hr,
            avg, obp, slg, iso,
            hr_per_pa, hit_per_pa,
            barrel_pct, hard_hit_pct, ev_avg, la_avg,
            window_type, vs_hand
          `)
          .eq('window_type', 'l14')
          .eq('vs_hand', 'A'),
      ])

      if (seasonRes.error) throw seasonRes.error
      if (l14Res.error)    throw l14Res.error

      rawSeasonRows.value = seasonRes.data || []
      rawL14Rows.value    = l14Res.data || []
    } catch (e) {
      console.error('[useTrends] loadStats failed:', e)
      error.value = e.message || 'Failed to load trends'
      rawSeasonRows.value = []
      rawL14Rows.value = []
    } finally {
      loading.value = false
    }
  }

  // Players in today's lineups. Used for "playing today" filter + matchup
  // chip rendering. We re-pull this when the day rolls.
  async function loadTodayLineups() {
    try {
      // Today's ET date — same algo as useSlate so we don't show stale
      const todayET = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'America/New_York',
        year: 'numeric', month: '2-digit', day: '2-digit',
      }).format(new Date())

      // Get today's games + lineups + opposing pitcher in one trip.
      // We'll need: lineup.player_id, lineup.team_id, game opponent team
      // and opposing pitcher (throws hand for matchup hint).
      const { data: games, error: gErr } = await supabase
        .from('games')
        .select(`
          id, game_date, status,
          home_team_id, away_team_id,
          home_pitcher_id, away_pitcher_id,
          home_team:teams!games_home_team_id_fkey ( id, abbrev, mlb_id ),
          away_team:teams!games_away_team_id_fkey ( id, abbrev, mlb_id ),
          home_pitcher:players!games_home_pitcher_id_fkey ( id, name, throws ),
          away_pitcher:players!games_away_pitcher_id_fkey ( id, name, throws )
        `)
        .eq('game_date', todayET)

      if (gErr) throw gErr
      if (!games || games.length === 0) {
        playingTodayIds.value = new Set()
        todayMatchupByBatter.value = new Map()
        return
      }

      const gameIds = games.map(g => g.id)
      const { data: lineups, error: lErr } = await supabase
        .from('lineups')
        .select(`
          game_id, team_id, batting_order,
          player:players!lineups_player_id_fkey ( id, name, mlbam_id, bats )
        `)
        .in('game_id', gameIds)

      if (lErr) throw lErr

      const ids = new Set()
      const matchups = new Map()

      for (const l of lineups || []) {
        if (!l.player?.id) continue
        ids.add(l.player.id)

        // Find the game + figure out who the opposing pitcher is.
        const game = games.find(g => g.id === l.game_id)
        if (!game) continue
        const isHome = l.team_id === game.home_team_id
        const opponent = isHome ? game.away_team : game.home_team
        const oppPitcher = isHome ? game.away_pitcher : game.home_pitcher
        matchups.set(l.player.id, {
          opponent_abbrev: opponent?.abbrev || null,
          pitcher_name: oppPitcher?.name || 'TBD',
          pitcher_throws: oppPitcher?.throws || null,
          game_id: game.id,
          game_status: game.status,
        })
      }

      playingTodayIds.value = ids
      todayMatchupByBatter.value = matchups
    } catch (e) {
      console.warn('[useTrends] loadTodayLineups failed (non-fatal):', e)
      // Non-fatal — the trends list still renders, the "playing today"
      // pill just shows zero matchups.
      playingTodayIds.value = new Set()
      todayMatchupByBatter.value = new Map()
    }
  }

  // Player metadata (name, team, bats, mlbam_id). We need this to render
  // headshots + team chips. One query for all batters with stats — cheap
  // since `players` is ~1500 rows total.
  const playersById = ref(new Map())
  const teamsById = ref(new Map())
  async function loadPlayersAndTeams() {
    try {
      const [pRes, tRes] = await Promise.all([
        supabase
          .from('players')
          .select('id, mlbam_id, name, bats, team_id')
          .eq('is_pitcher', false),
        supabase
          .from('teams')
          .select('id, abbrev, name, mlb_id'),
      ])
      if (pRes.error) throw pRes.error
      if (tRes.error) throw tRes.error

      const pMap = new Map()
      for (const p of pRes.data || []) pMap.set(p.id, p)
      playersById.value = pMap

      const tMap = new Map()
      for (const t of tRes.data || []) tMap.set(t.id, t)
      teamsById.value = tMap
    } catch (e) {
      console.error('[useTrends] loadPlayersAndTeams failed:', e)
      // We can't render without these — surface the error.
      error.value = e.message || 'Failed to load players'
    }
  }

  // The actual computed trend rows. This is where the metric extractor +
  // trend score get applied, plus joins with metadata + today filters.
  //
  // For each batter we always compute trend scores on ALL four base
  // metrics, even when the user has a single metric selected. This is
  // cheap and lets the Combined view light up without re-iterating.
  // The active metric just controls which trend_score gets surfaced as
  // the row's primary trend_score / metric_l14 / metric_season values.
  const rows = computed(() => {
    const activeMetric = metric.value

    // Index L14 by batter_id for O(1) lookup
    const l14ByBatter = new Map()
    for (const r of rawL14Rows.value) l14ByBatter.set(r.batter_id, r)

    const out = []
    for (const seasonRow of rawSeasonRows.value) {
      const l14 = l14ByBatter.get(seasonRow.batter_id)
      if (!l14) continue   // Need both windows to compute trend
      if ((l14.pa || 0) < minPA.value) continue

      // ── Pass 1: compute trend scores on all base metrics ──
      // Keep the underlying L14 + season values too so we can surface
      // them on the active row.
      const perMetric = {}   // { hr: {l14, season, ts}, hits: {...}, ... }
      for (const m of BASE_METRICS) {
        const ext = METRIC_EXTRACTORS[m]
        const ml14    = ext(l14)
        const mseason = ext(seasonRow)
        const ts = computeTrendScore(ml14, mseason)
        perMetric[m] = { l14: ml14, season: mseason, ts }
      }

      // ── Pass 2: derive combined heat ──
      const tsByMetric = {}
      for (const m of BASE_METRICS) tsByMetric[m] = perMetric[m].ts
      const combinedTs = computeCombinedTrend(tsByMetric)

      // ── Pass 3: pick which trend score the row surfaces ──
      // For base metrics, surface that metric's trend + L14/SZN values.
      // For combined, surface the combined score and use HR/PA as the
      // representative anchor for the L14/SZN bars (most interpretable
      // metric for the typical user). If HR/PA is zero or missing (e.g.
      // a slap hitter with no homers), fall back to the first base
      // metric with non-zero values so the bars actually render.
      //
      // anchorMetric tells the consuming component which unit to use
      // when formatting the L14/SZN cell values (ISO is .234-style,
      // others are percent).
      let activeTs, activeL14, activeSeason, anchorMetric
      if (activeMetric === 'combined') {
        activeTs = combinedTs
        const hasNonZero = (p) =>
          p?.l14 != null && p?.season != null && (p.l14 > 0 || p.season > 0)
        if (hasNonZero(perMetric.hr)) {
          anchorMetric = 'hr'
        } else {
          anchorMetric = BASE_METRICS.find(m => hasNonZero(perMetric[m])) || 'hr'
        }
        const anchor = perMetric[anchorMetric]
        activeL14 = anchor?.l14 ?? null
        activeSeason = anchor?.season ?? null
      } else {
        anchorMetric = activeMetric
        const p = perMetric[activeMetric]
        activeTs = p?.ts
        activeL14 = p?.l14 ?? null
        activeSeason = p?.season ?? null
      }

      if (activeTs == null) continue
      if (activeL14 == null || activeSeason == null) continue

      const player = playersById.value.get(seasonRow.batter_id)
      // We can't render without a player record (no name/headshot). Skip
      // these silently — they're usually mid-season call-ups whose row
      // hasn't been backfilled.
      if (!player) continue

      const team = player.team_id ? teamsById.value.get(player.team_id) : null
      const matchup = todayMatchupByBatter.value.get(player.id) || null

      out.push({
        batter: {
          id: player.id,
          mlbam_id: player.mlbam_id,
          name: player.name,
          bats: player.bats,
          team_id: player.team_id,
          team_abbrev: team?.abbrev || null,
          team_mlb_id: team?.mlb_id || null,
        },
        pa_l14:    l14.pa,
        pa_season: seasonRow.pa,
        metric_l14:    activeL14,
        metric_season: activeSeason,
        metric_delta:  activeL14 - activeSeason,
        trend_score:   activeTs,
        // What metric the L14/SZN/delta values actually represent. For
        // base metrics this matches the active metric. For combined it
        // tells the component which formatter to use (usually 'hr',
        // sometimes 'iso'/'hits'/'barrel' if HR fallback fired).
        anchor_metric: anchorMetric,
        // All per-metric trend scores exposed for components that want
        // to render a multi-signal mini-display (e.g. Combined card
        // could show four little dots, one per metric, colored by tier)
        trend_by_metric: tsByMetric,
        combined_trend: combinedTs,
        // Raw counters used by the row's secondary line
        hr_l14:    l14.hr,
        hr_season: seasonRow.hr,
        hits_l14:    l14.hits,
        hits_season: seasonRow.hits,
        playing_today: playingTodayIds.value.has(player.id),
        today_matchup: matchup,
      })
    }
    return out
  })

  // Sorted + filtered for display
  const displayRows = computed(() => {
    let list = rows.value

    if (playingTodayOnly.value) {
      list = list.filter(r => r.playing_today)
    }

    // Direction = sort key. Hot = highest trend_score first, Cold = lowest.
    const dirMult = direction.value === 'cold' ? 1 : -1
    return [...list].sort((a, b) => dirMult * (a.trend_score - b.trend_score))
  })

  // Snapshot counts for the header strip
  const stats = computed(() => ({
    total_qualified: rows.value.length,
    playing_today: rows.value.filter(r => r.playing_today).length,
    hot_count: rows.value.filter(r => r.trend_score > 0.10).length,
    cold_count: rows.value.filter(r => r.trend_score < -0.10).length,
  }))

  // Realtime: if batter_stats updates (nightly cron), refresh
  let channel = null
  let dayCheckTimer = null
  onMounted(async () => {
    await Promise.all([
      loadStats(),
      loadPlayersAndTeams(),
      loadTodayLineups(),
    ])

    channel = supabase
      .channel('trends-changes')
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'batter_stats' },
          () => loadStats())
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'lineups' },
          () => loadTodayLineups())
      .subscribe()

    // Roll-over: poll for date changes every 5 minutes. Avoids stale
    // "playing today" lists if the user leaves the tab open overnight.
    dayCheckTimer = setInterval(() => loadTodayLineups(), 5 * 60 * 1000)
  })

  onUnmounted(() => {
    if (channel) supabase.removeChannel(channel)
    if (dayCheckTimer) clearInterval(dayCheckTimer)
  })

  return {
    // Data
    rows: displayRows,
    allRows: rows,
    stats,
    loading,
    error,
    // UI state
    metric,
    direction,
    playingTodayOnly,
    minPA,
    // Constants
    METRIC_LABELS,
    TREND_METRICS,
    // Actions
    refresh: async () => {
      await Promise.all([loadStats(), loadTodayLineups()])
    },
  }
}
