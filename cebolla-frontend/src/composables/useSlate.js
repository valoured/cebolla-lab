import { ref, onMounted, onUnmounted } from 'vue'
import { supabase } from '../supabase.js'
import { ping } from './useRealtimePulse.js'

/**
 * Loads the active slate for display.
 *
 * Smart date selection:
 * - If `dateStr` is passed at call time, uses that exact date (legacy path).
 * - If the user has called setTargetDate(d), uses that.
 * - Otherwise: picks the EARLIEST upcoming date that has at least one non-final game
 *   (so when today's slate is all over, the slate auto-advances to tomorrow's games)
 *
 * Also fetches a SHORT window of nearby slate dates (yesterday → today+7) so the
 * SlateView can render a DateNav with multiple selectable dates.
 */
export function useSlate(dateStr) {
  const games = ref([])
  const loading = ref(true)
  const error = ref(null)
  const activeDate = ref(null)         // 'YYYY-MM-DD' of currently-displayed slate
  const availableDates = ref([])       // ['YYYY-MM-DD', ...] sorted ascending
  const targetDate = ref(dateStr || null)  // user override; null = auto-pick

  let reloadTimer = null
  let channel = null
  // Monotonic token to discard stale load() results. When rapid setTargetDate
  // clicks fire multiple overlapping loads, only the most recent load is
  // allowed to write to games.value. Without this guard the slower load
  // could finish second and overwrite the user's actual selection.
  let loadToken = 0

  function setTargetDate(d) {
    // null reverts to auto-pick. Anything else forces that date.
    targetDate.value = d
    load()
  }

  // ── ET-aware date helpers ─────────────────────────────────────────────
  function todayETIso() {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'America/New_York',
      year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(new Date())
  }

  function addDays(iso, n) {
    // Noon-UTC anchor avoids TZ-edge weirdness
    const d = new Date(iso + 'T12:00:00Z')
    d.setUTCDate(d.getUTCDate() + n)
    return d.toISOString().slice(0, 10)
  }

  // ── Active date selection ─────────────────────────────────────────────
  // Real MLB-aware logic — a "slate day" doesn't end at midnight UTC or
  // midnight ET, it ends when all that day's games are FINAL. West-coast
  // night games routinely run past midnight ET; the slate should stay
  // "yesterday" until those games settle, not flip to "today" just because
  // the clock rolled over.
  //
  // Both queries run in parallel — previously they were sequential, with
  // step 2 only running if step 1 was empty. Parallelizing saves ~150ms on
  // typical loads (yesterday's games are usually all final, so step 1 was
  // a wasted round-trip before step 2 fired). Step 1 still wins if it has
  // a hit; step 2's result is discarded in that case.
  async function pickActiveDate() {
    // Explicit user override (set via setTargetDate) wins
    if (targetDate.value) return targetDate.value

    const todayET = todayETIso()
    const yesterdayET = addDays(todayET, -1)

    const [yRes, futureRes] = await Promise.all([
      // (1) Check yesterday — if any game is still non-final, slate is yesterday
      supabase
        .from('games')
        .select('game_date, status')
        .eq('game_date', yesterdayET)
        .not('status', 'in', '("Final","Game Over","Completed Early","Postponed","Cancelled","Forfeit")')
        .limit(1),

      // (2) Today or future — earliest date with at least one non-final game
      supabase
        .from('games')
        .select('game_date, status')
        .gte('game_date', todayET)
        .not('status', 'in', '("Final","Game Over","Completed Early","Postponed","Cancelled","Forfeit")')
        .order('game_date', { ascending: true })
        .limit(1),
    ])

    if (!yRes.error && yRes.data && yRes.data.length > 0) {
      return yesterdayET
    }

    if (futureRes.error || !futureRes.data || futureRes.data.length === 0) {
      return todayET
    }
    return futureRes.data[0].game_date
  }

  // ── Available dates for the date nav ─────────────────────────────────
  // Returns YYYY-MM-DD list of dates that have ANY games in DB, within a
  // sensible window (yesterday → today+6). Sorted ascending.
  async function fetchAvailableDates() {
    const todayET = todayETIso()
    const start = addDays(todayET, -1)
    const end = addDays(todayET, 6)

    const { data, error: e } = await supabase
      .from('games')
      .select('game_date')
      .gte('game_date', start)
      .lte('game_date', end)
      .order('game_date', { ascending: true })

    if (e || !data) return []
    // Deduplicate (the query returns one row per game)
    const seen = new Set()
    const out = []
    for (const r of data) {
      const d = r.game_date
      if (d && !seen.has(d)) {
        seen.add(d)
        out.push(d)
      }
    }
    return out
  }

  // ── Main load ─────────────────────────────────────────────────────────
  async function load() {
    const myToken = ++loadToken
    loading.value = true
    error.value = null

    // Parallel: pick the date AND fetch the available-dates list
    const [picked, dateList] = await Promise.all([
      pickActiveDate(),
      fetchAvailableDates(),
    ])
    // Bail out if a newer load() has been kicked off while we were waiting.
    if (myToken !== loadToken) return
    activeDate.value = picked
    availableDates.value = dateList

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
        away_team:teams!games_away_team_id_fkey ( id, abbrev, name, stadium, mlb_id, home_plate_bearing ),
        home_team:teams!games_home_team_id_fkey ( id, abbrev, name, stadium, mlb_id, home_plate_bearing ),
        away_pitcher:players!games_away_pitcher_id_fkey ( id, name ),
        home_pitcher:players!games_home_pitcher_id_fkey ( id, name )
      `)
      .eq('game_date', picked)
      .order('game_time_utc', { ascending: true })

    // Final staleness check before mutating state — protects against the
    // case where ANOTHER load() ran in parallel and finished first.
    if (myToken !== loadToken) return

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

    // Slate cares about games changes (scores, statuses, lineup changes
    // surfaced via the joined fields). We deliberately do NOT subscribe to
    // projections — the slate doesn't display any projection-derived data,
    // so a projection update for one of 60+ batters firing a slate reload
    // is pure waste.
    channel = supabase
      .channel('slate-changes')
      .on('postgres_changes',
          { event: '*', schema: 'public', table: 'games' },
          () => scheduleReload())
      .subscribe()
  })

  onUnmounted(() => {
    if (reloadTimer) clearTimeout(reloadTimer)
    if (channel) supabase.removeChannel(channel)
  })

  return {
    games,
    loading,
    error,
    activeDate,
    availableDates,
    targetDate,
    setTargetDate,
    reload: load,
  }
}
