import { ref, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { ping } from './useRealtimePulse.js'

/**
 * Loads the active slate for display.
 *
 * Smart date selection:
 * - If `dateStr` is passed, uses that exact date
 * - Otherwise: picks the EARLIEST upcoming date that has at least one non-final game
 *   (so when today's slate is all over, the slate auto-advances to tomorrow's games)
 */
export function useSlate(dateStr) {
  const games = ref([])
  const loading = ref(true)
  const error = ref(null)
  const activeDate = ref(null)   // 'YYYY-MM-DD' string of the date currently being shown

  let reloadTimer = null
  let channel = null

  // Step 1: figure out which date to load
  async function pickActiveDate() {
    if (dateStr) return dateStr

    const today = new Date().toISOString().slice(0, 10)

    // Find the earliest date >= today that has at least one non-final game
    const { data, error: e } = await supabase
      .from('games')
      .select('game_date, status')
      .gte('game_date', today)
      .not('status', 'in', '("Final","Game Over","Completed Early")')
      .order('game_date', { ascending: true })
      .limit(1)

    if (e || !data || data.length === 0) {
      // Fallback: just use today (will show empty state)
      return today
    }
    return data[0].game_date
  }

  async function load() {
    loading.value = true
    error.value = null

    const targetDate = await pickActiveDate()
    activeDate.value = targetDate

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
        home_team:teams!games_home_team_id_fkey ( id, abbrev, name, stadium, mlb_id, home_plate_bearing ),
        away_pitcher:players!games_away_pitcher_id_fkey ( id, name ),
        home_pitcher:players!games_home_pitcher_id_fkey ( id, name )
      `)
      .eq('game_date', targetDate)
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

  return { games, loading, error, activeDate, reload: load }
}
