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
  //
  // Real MLB-aware logic — a "slate day" doesn't end at midnight UTC or
  // midnight ET, it ends when all that day's games are FINAL. West-coast
  // night games routinely run past midnight ET; the slate should stay
  // "yesterday" until those games settle, not flip to "today" just because
  // the clock rolled over.
  //
  // Algorithm:
  //   1. Get today's ET date (DST-safe via Intl.DateTimeFormat)
  //   2. Compute yesterday's ET date
  //   3. If yesterday has ANY non-final games → slate = yesterday (still active)
  //   4. Otherwise → slate = earliest date >= today with non-final games
  //   5. Final fallback → today (empty state)
  async function pickActiveDate() {
    if (dateStr) return dateStr

    // ET date today (not UTC — DST-safe)
    const todayET = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(new Date())

    // Yesterday's ET date (subtract one day from todayET)
    const yesterdayDate = new Date(todayET + 'T12:00:00Z')  // noon UTC anchor avoids TZ-edge weirdness
    yesterdayDate.setUTCDate(yesterdayDate.getUTCDate() - 1)
    const yesterdayET = yesterdayDate.toISOString().slice(0, 10)

    // (1) Check yesterday — if any game is still non-final, slate is yesterday
    const { data: yData, error: yErr } = await supabase
      .from('games')
      .select('game_date, status')
      .eq('game_date', yesterdayET)
      .not('status', 'in', '("Final","Game Over","Completed Early","Postponed","Cancelled","Forfeit")')
      .limit(1)

    if (!yErr && yData && yData.length > 0) {
      return yesterdayET
    }

    // (2) Today or future — earliest date with at least one non-final game
    const { data, error: e } = await supabase
      .from('games')
      .select('game_date, status')
      .gte('game_date', todayET)
      .not('status', 'in', '("Final","Game Over","Completed Early","Postponed","Cancelled","Forfeit")')
      .order('game_date', { ascending: true })
      .limit(1)

    if (e || !data || data.length === 0) {
      // (3) Final fallback: today (will show empty state if nothing scheduled)
      return todayET
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
        home_team:teams!games_home_team_id_fkey ( id, abbrev, name, stadium, mlb_id ),
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
