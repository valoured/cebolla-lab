import { ref } from 'vue'
import { supabase } from '../supabase.js'

/**
 * Loads everything the HR Report needs for one game.
 * Now also pulls projections rows so the Edge column can render real values.
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
          id, batting_order, position, bats, is_confirmed, team_id,
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

      // 4) Batter stats
      const batterIds = [...awayLineup.value, ...homeLineup.value]
        .map(l => l.player?.id)
        .filter(Boolean)
      if (batterIds.length) {
        const { data: bs } = await supabase
          .from('batter_stats')
          .select('*')
          .in('batter_id', batterIds)
          .eq('window_type', 'season')
          .eq('vs_hand', 'A')
        const map = {}
        for (const r of (bs || [])) map[r.batter_id] = r
        batterStats.value = map
      }

      // 5) Odds (latest snapshot per player+market)
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
          if (!oddsMap[row.player_id][row.market]) {
            oddsMap[row.player_id][row.market] = row
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

  load()

  return {
    game, awayLineup, homeLineup,
    arsenalAway, arsenalHome,
    batterStats, odds, bvp, projections,
    loading, error, reload: load,
  }
}
