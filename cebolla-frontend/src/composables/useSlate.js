import { ref, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { ping } from './useRealtimePulse.js'

/**
 * Loads the active slate for display.
 *
 * Smart date selection:
 * - If `initialDateStr` is passed at composition time, uses that exact date as the starting target.
 * - Otherwise: auto-picks the EARLIEST upcoming date that has at least one non-final game
 *   (so when today's slate is all over, the slate auto-advances to tomorrow's games).
 *
 * Multi-day nav:
 * - `availableDates` is the list of distinct game_dates >= today found in the DB.
 * - `setTargetDate(dateStr | null)` overrides the auto-pick. Pass `null` to return to auto mode.
 * - `isAutoPicked` is true when no override is active (the date shown was chosen by the system).
 */
export function useSlate(initialDateStr) {
  const games = ref([])
  const loading = ref(true)
  const error = ref(null)
  const activeDate = ref(null)         // 'YYYY-MM-DD' string of the date currently being shown
  const availableDates = ref([])       // distinct upcoming game_dates in the DB
  const targetDate = ref(initialDateStr || null)  // user override; null = auto-pick

  let reloadTimer = null
  let datesReloadTimer = null
  let channel = null

  function todayStr() {
    // Use local date components, NOT toISOString() — that returns UTC,
    // which flips a day ahead for ET evenings (8 PM ET = midnight UTC).
    const d = new Date()
    const y = d.getFullYear()
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  }

  // Step 1: figure out which date to load
  async function pickActiveDate() {
    if (targetDate.value) return targetDate.value

    const today = todayStr()

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

  // Pull the list of upcoming distinct dates for the DateNav.
  // We grab today + future, sorted ascending. Capped at 14 days out to keep it sane.
  async function loadAvailableDates() {
    const today = todayStr()
    const { data, error: e } = await supabase
      .from('games')
      .select('game_date')
      .gte('game_date', today)
      .order('game_date', { ascending: true })

    if (e || !data) {
      availableDates.value = [today]
      return
    }

    // distinct + cap
    const seen = new Set()
    const dates = []
    for (const row of data) {
      if (row.game_date && !seen.has(row.game_date)) {
        seen.add(row.game_date)
        dates.push(row.game_date)
        if (dates.length >= 14) break
      }
    }

    // Always make sure today is in the list, even if no games scheduled
    if (!seen.has(today)) {
      dates.unshift(today)
    }

    availableDates.value = dates
  }

  async function load() {
    loading.value = true
    error.value = null

    const dateToLoad = await pickActiveDate()
    activeDate.value = dateToLoad

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
      .eq('game_date', dateToLoad)
      .order('game_time_utc', { ascending: true })

    if (dbErr) {
      error.value = dbErr.message
      games.value = []
    } else {
      games.value = data || []
    }
    loading.value = false
  }

  // Public setter for DateNav. Pass null to clear and return to auto-pick.
  function setTargetDate(dateStr) {
    targetDate.value = dateStr || null
    load()
  }

  // Debounced reload — batch bursts so we don't hammer the DB
  function scheduleReload() {
    if (reloadTimer) clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      load()
      ping()
    }, 1500)
  }

  // Separate debounce for the dates list — it doesn't need to refresh as often
  function scheduleDatesReload() {
    if (datesReloadTimer) clearTimeout(datesReloadTimer)
    datesReloadTimer = setTimeout(() => {
      loadAvailableDates()
    }, 5000)
  }

  onMounted(async () => {
    // Load dates first so DateNav has something to render alongside the slate
    await loadAvailableDates()
    await load()

    channel = supabase
      .channel('slate-changes')
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'games' },
          () => {
            scheduleReload()
            scheduleDatesReload()
          })
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'projections' },
          () => scheduleReload())
      .subscribe()
  })

  onUnmounted(() => {
    if (reloadTimer) clearTimeout(reloadTimer)
    if (datesReloadTimer) clearTimeout(datesReloadTimer)
    if (channel) supabase.removeChannel(channel)
  })

  return {
    games,
    loading,
    error,
    activeDate,
    availableDates,
    targetDate,        // exposed read-only-ish; mutate via setTargetDate
    setTargetDate,
    reload: load,
  }
}
