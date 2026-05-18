import { ref, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { ping } from './useRealtimePulse.js'

export function useSlate(dateStr) {
  const games = ref([])
  const loading = ref(true)
  const error = ref(null)

  let reloadTimer = null
  let channel = null

  async function load() {
    loading.value = true
    error.value = null

    const today = dateStr || new Date().toISOString().slice(0, 10)

    const { data, error: dbErr } = await supabase
      .from('games')
      .select(`
        id,
        mlb_game_pk,
        game_date,
        game_time_utc,
        venue,
        status,
        temp_f,
        wind_mph,
        wind_label,
        precip_pct,
        hr_factor_overall,
        hr_factor_lhb,
        hr_factor_rhb,
        home_score,
        away_score,
        current_inning,
        inning_state,
        away_team:teams!games_away_team_id_fkey ( id, abbrev, name, stadium, mlb_id ),
        home_team:teams!games_home_team_id_fkey ( id, abbrev, name, stadium, mlb_id ),
        away_pitcher:players!games_away_pitcher_id_fkey ( id, name ),
        home_pitcher:players!games_home_pitcher_id_fkey ( id, name )
      `)
      .eq('game_date', today)
      .order('game_time_utc', { ascending: true })

    if (dbErr) {
      error.value = dbErr.message
      games.value = []
    } else {
      games.value = data || []
    }
    loading.value = false
  }

  // Debounced reload — batch bursts so we don't hammer the DB
  function scheduleReload() {
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      load()
      ping()
    }, 1500)
  }

  onMounted(() => {
    load()

    // Realtime: subscribe to games changes (scores, status, inning state)
    channel = supabase
      .channel('slate-changes')
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'games' },
          () => scheduleReload())
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'projections' },
          () => scheduleReload())
      .subscribe()
  })

  onUnmounted(() => {
    if (reloadTimer) clearTimeout(reloadTimer)
    if (channel) supabase.removeChannel(channel)
  })

  return { games, loading, error, reload: load }
}
