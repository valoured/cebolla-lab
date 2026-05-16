import { ref, computed } from 'vue'
import { supabase } from '../supabase.js'

/**
 * Loads everything the HR Report needs for one game:
 *  - game header (teams, pitchers, weather, HR factors)
 *  - lineups for both teams (sorted by batting order)
 *  - batter stats for every batter in the lineups
 *  - pitcher arsenals for both starting pitchers
 *  - current DK odds (HR / hits / RBI) for everyone in the lineups
 *  - BvP history for every batter against the opposing starter
 */
export function useGame(gameId) {
  const game        = ref(null)
  const awayLineup  = ref([])
  const homeLineup  = ref([])
  const arsenalAway = ref([])
  const arsenalHome = ref([])
  const batterStats = ref({})   // {player_id: row}
  const odds        = ref({})   // {player_id: { market: {american, line, ...} }}
  const bvp         = ref({})   // {`${batter_id}_${pitcher_id}`: row}
  const loading     = ref(true)
  const error       = ref(null)

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
          away_team:teams!games_away_team_id_fkey ( id, abbrev, name, stadium ),
          home_team:teams!games_home_team_id_fkey ( id, abbrev, name, stadium ),
          away_pitcher:players!games_away_pitcher_id_fkey ( id, mlbam_id, name, throws ),
          home_pitcher:players!games_home_pitcher_id_fkey ( id, mlbam_id, name, throws )
        `)
        .eq('id', gameId)
        .single()
      if (gErr) throw gErr
      game.value = g

      // 2) Lineups for both teams
      const { data: lns } = await supabase
        .from('lineups')
        .select(`
          id, batting_order, position, bats, is_confirmed, team_id,
          player:players ( id, mlbam_id, name, position, bats )
        `)
        .eq('game_id', gameId)
        .order('batting_order', { ascending: true })

      awayLineup.value = (lns || []).filter(l => l.team_id === g.away_team.id)
      homeLineup.value = (lns || []).filter(l => l.team_id === g.home_team.id)

      // 3) Pitcher arsenals
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

      // 4) Batter stats for every batter in either lineup
      const batterIds = [...awayLineup.value, ...homeLineup.value]
        .map(l => l.player?.id)
        .filter(Boolean)
      if (batterIds.length) {
        const { data: bs } = await supabase
          .from('batter_stats')
          .select('*')
          .in('batter_id', batterIds)
          .eq('window_type', 'season')
          .eq('vs_hand', 'A')   // season totals vs all
        const map = {}
        for (const r of (bs || [])) map[r.batter_id] = r
        batterStats.value = map
      }

      // 5) Current odds (HR / Hits / RBI markets) for this game
      // We rely on `is_current` being true for the latest snapshot.
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
          if (!oddsMap[row.player_id]) oddsMap[row.player_id] = {}
          // Only keep the first (most recent) seen per market
          if (!oddsMap[row.player_id][row.market]) {
            oddsMap[row.player_id][row.market] = row
          }
        }
        odds.value = oddsMap
      }

      // 6) BvP history: each batter vs the opposing starter
      const bvpQueries = []
      if (g.home_pitcher?.id) {
        // Away batters vs home pitcher
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

      loading.value = false
    } catch (e) {
      error.value = e.message || String(e)
      loading.value = false
    }
  }

  load()

  return {
    game, awayLineup, homeLineup,
    arsenalAway, arsenalHome,
    batterStats, odds, bvp,
    loading, error, reload: load,
  }
}
