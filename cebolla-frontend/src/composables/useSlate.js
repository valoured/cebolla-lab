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

    // ET-relative "today" — same convention used by the picker scripts and PODView.
    // BUG fix: previously used toISOString() which returns UTC. In ET evenings
    // (after 8 PM ET = midnight UTC) that flipped the date to tomorrow and the
    // slate auto-advanced before the day was over. Always use the ET calendar.
    const today = etTodayStr()

    // Does today's slate still have ANY game that hasn't finalized?
    // If so, stay on today — even if the only remaining games are mid-inning.
    // Only advance to tomorrow once today's entire slate is settled.
    const { data: todayData, error: todayErr } = await supabase
      .from('games')
      .select('id, status')
      .eq('game_date', today)
      .not('status', 'in', '("Final","Game Over","Completed Early")')
      .limit(1)

    if (!todayErr && todayData && todayData.length > 0) {
      return today
    }

    // Today's slate is fully settled (or has zero games). Look forward for the
    // earliest upcoming date with a non-final game.
    const { data, error: e } = await supabase
      .from('games')
      .select('game_date, status')
      .gt('game_date', today)
      .not('status', 'in', '("Final","Game Over","Completed Early")')
      .order('game_date', { ascending: true })
      .limit(1)

    if (e || !data || data.length === 0) {
      // No upcoming games — keep showing today (will render empty/settled state)
      return today
    }
    return data[0].game_date
  }

  // ET-relative YYYY-MM-DD (avoids UTC drift in evenings)
  function etTodayStr() {
    const now = new Date()
    // EDT is UTC-4; EST is UTC-5. We assume EDT during baseball season.
    // For correctness year-round, we'd need a proper TZ library, but EDT
    // covers the entire MLB regular season (March through October).
    const etMs = now.getTime() - 4 * 60 * 60 * 1000
    const et = new Date(etMs)
    const y = et.getUTCFullYear()
    const m = String(et.getUTCMonth() + 1).padStart(2, '0')
    const d = String(et.getUTCDate()).padStart(2, '0')
    return `${y}-${m}-${d}`
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
        wind_dir_deg,
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
