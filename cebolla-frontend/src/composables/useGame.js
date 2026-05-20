import { ref, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { ping } from './useRealtimePulse.js'

/**
 * Loads everything the HR Report needs for one game.
 * Now also pulls projections rows so the Edge column can render real values.
 * Subscribes to realtime changes for this game_id and reloads on burst.
 */
export function useGame(gameId) {
  const game        = ref(null)
  const awayLineup  = ref([])
  const homeLineup  = ref([])
  const arsenalAway = ref([])
  const arsenalHome = ref([])
  const batterStats = ref({})
  const odds        = ref({})
  const bvp         = ref({})
  const projections = ref({})   // {`${player_id}_${market}`: row}
  const loading     = ref(true)
  const error       = ref(null)

  let reloadTimer = null
  let channel = null

  async function load() {
    loading.value = true
    error.value = null
    try {
      // 1) Game header
      const { data: g, error: gErr } = await supabase
        .from('games')
        .select(`
          id, mlb_game_pk, game_date, game_time_utc, venue, status,
          temp_f, wind_mph, wind_label, precip_pct,
          hr_factor_overall, hr_factor_lhb, hr_factor_rhb,
          home_score, away_score, current_inning, inning_state,
          away_team:teams!games_away_team_id_fkey ( id, abbrev, name, stadium, mlb_id ),
          home_team:teams!games_home_team_id_fkey ( id, abbrev, name, stadium, mlb_id ),
          away_pitcher:players!games_away_pitcher_id_fkey ( id, mlbam_id, name, throws ),
          home_pitcher:players!games_home_pitcher_id_fkey ( id, mlbam_id, name, throws )
        `)
        .eq('id', gameId)
        .single()
      if (gErr) throw gErr
      game.value = g

      // 2) Lineups
      const { data: lns } = await supabase
        .from('lineups')
        .select(`
          id, batting_order, position, bats, is_confirmed, source, team_id,
          player:players ( id, mlbam_id, name, position, bats )
        `)
        .eq('game_id', gameId)
        .order('batting_order', { ascending: true })

      awayLineup.value = (lns || []).filter(l => l.team_id === g.away_team.id)
      homeLineup.value = (lns || []).filter(l => l.team_id === g.home_team.id)

      // 3) Arsenals
      const pitcherIds = [g.away_pitcher?.id, g.home_pitcher?.id].filter(Boolean)
      if (pitcherIds.length) {
        const { data: ars } = await supabase
          .from('pitcher_arsenals')
          .select('*')
          .in('pitcher_id', pitcherIds)
          .eq('window_type', 'season')
        arsenalAway.value = (ars || []).filter(a => a.pitcher_id === g.away_pitcher?.id)
        arsenalHome.value = (ars || []).filter(a => a.pitcher_id === g.home_pitcher?.id)
      }

      // 4) Batter stats — REMOVED in Phase 7
      //
      // BatterTable now fetches its own Statcast via useStatcastBatters
      // composable for window-aware (Season / L30 / L14 / L7) data.
      // Keeping batterStats fetch here would have caused duplicate queries.
      //
      // If a future view needs batter season stats outside of BatterTable,
      // consider extracting useStatcastBatters or building a small
      // useBatterSeasonStats helper instead.

      // Player IDs are still needed for odds / BvP / projections queries below.
      const batterIds = [...awayLineup.value, ...homeLineup.value]
        .map(l => l.player?.id)
        .filter(Boolean)

      // 5) Odds (latest snapshot per player+market+line)
      //
      // Keyed as: oddsMap[player_id][market][line] = row
      //
      // Many markets (hits, h_r_rbi) have multiple lines all stored under
      // the same `market` string but different `line` values. The previous
      // version of this loop keyed by market only and silently dropped
      // 3-of-4 hits rows and 4-of-5 HRR rows. Now each line is preserved
      // and consumers (BatterTable / BatterCard) look up the line they
      // need explicitly.
      //
      // For single-line markets (HR Anytime = line 0.5) the structure is
      // the same — consumers just look up `odds[pid].hr_anytime_yes[0.5]`.
      if (batterIds.length) {
        const { data: o } = await supabase
          .from('odds_snapshots')
          .select('*')
          .eq('game_id', gameId)
          .eq('is_current', true)
          .in('player_id', batterIds)
          .order('snapshot_time', { ascending: false })
        const oddsMap = {}
        for (const row of (o || [])) {
          const pid = row.player_id
          const mkt = row.market
          const line = row.line == null ? 0.5 : Number(row.line)
          if (!oddsMap[pid]) oddsMap[pid] = {}
          if (!oddsMap[pid][mkt]) oddsMap[pid][mkt] = {}
          // Order-by-snapshot-desc + only-set-if-empty preserves the latest
          // snapshot per (pid, market, line) tuple.
          if (!oddsMap[pid][mkt][line]) {
            oddsMap[pid][mkt][line] = row
          }
        }
        odds.value = oddsMap
      }

      // 6) BvP
      const bvpQueries = []
      if (g.home_pitcher?.id) {
        const awayBatterIds = awayLineup.value.map(l => l.player?.id).filter(Boolean)
        if (awayBatterIds.length) {
          bvpQueries.push(
            supabase.from('bvp_history').select('*')
              .eq('pitcher_id', g.home_pitcher.id)
              .in('batter_id', awayBatterIds)
          )
        }
      }
      if (g.away_pitcher?.id) {
        const homeBatterIds = homeLineup.value.map(l => l.player?.id).filter(Boolean)
        if (homeBatterIds.length) {
          bvpQueries.push(
            supabase.from('bvp_history').select('*')
              .eq('pitcher_id', g.away_pitcher.id)
              .in('batter_id', homeBatterIds)
          )
        }
      }
      const bvpResults = await Promise.all(bvpQueries)
      const bvpMap = {}
      for (const res of bvpResults) {
        for (const r of (res.data || [])) {
          bvpMap[`${r.batter_id}_${r.pitcher_id}`] = r
        }
      }
      bvp.value = bvpMap

      // 7) Projections — keyed by (player_id + market)
      if (batterIds.length) {
        const { data: projs } = await supabase
          .from('projections')
          .select('*')
          .eq('game_id', gameId)
          .in('player_id', batterIds)
          .order('created_at', { ascending: false })
        const projMap = {}
        for (const row of (projs || [])) {
          const key = `${row.player_id}_${row.market}`
          if (!projMap[key]) projMap[key] = row   // keep newest only
        }
        projections.value = projMap
      }

      loading.value = false
    } catch (e) {
      error.value = e.message || String(e)
      loading.value = false
    }
  }

  // Debounced reload — batch bursts during e.g. odds/projections write cycles
  function scheduleReload() {
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      load()
      ping()
    }, 1500)
  }

  onMounted(() => {
    load()

    // Realtime: subscribe to relevant tables, filtered to this game_id where applicable.
    // game_id is an integer; Supabase Realtime filter syntax: `column=eq.value`
    const filter = `game_id=eq.${gameId}`

    channel = supabase
      .channel(`game-${gameId}-changes`)
      // The `games` table uses `id` not `game_id`, so different filter
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'games', filter: `id=eq.${gameId}` },
          () => scheduleReload())
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'lineups', filter },
          () => scheduleReload())
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'odds_snapshots', filter },
          () => scheduleReload())
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'projections', filter },
          () => scheduleReload())
      .subscribe()
  })

  onUnmounted(() => {
    if (reloadTimer) clearTimeout(reloadTimer)
    if (channel) supabase.removeChannel(channel)
  })

  return {
    game, awayLineup, homeLineup,
    arsenalAway, arsenalHome,
    batterStats, odds, bvp, projections,
    loading, error, reload: load,
  }
}
