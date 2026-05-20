/**
 * useTodaysPOD.js — Fetches today's Plays of the Day (HR + HRR) once per
 * session and exposes helpers for "is this player a POD?" checks across the site.
 *
 * Used by:
 *   - BatterTable / BatterCard (gold badge on player rows)
 *   - GameCard on the slate (small gold pip when the game has a POD)
 *   - PlayerView (header tag when viewing a POD player)
 *
 * Single shared module-level cache. All consumers see the same answer.
 * Refreshes when the user returns to the tab after being away >5 min.
 *
 * v2: handles multiple PODs per day (HR + HRR). isPOD/isPODGame return true
 * if ANY of today's PODs matches. Previously only fetched one row which
 * caused the badge to silently disappear after HRR launched.
 */

import { ref, computed } from 'vue'
import { supabase } from '../supabase.js'

// ── Module-level cache ─────────────────────────────────────────────
let _podPromise = null
let _lastFetchedAt = null
const _today = ref(null)              // ISO date string of fetched PODs' pod_date
const _pods = ref([])                 // ALL POD rows for today (array, possibly empty)

/** ET-relative today (DST-safe via Intl.DateTimeFormat). */
function getTodayIso() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
}

async function fetchPODs() {
  const today = getTodayIso()
  const { data, error } = await supabase
    .from('pods')
    .select('id, pod_date, market_class, game_id, player_id, player_mlbam_id, ' +
            'player_name, team_abbrev, opponent_abbrev, market, status')
    .eq('pod_date', today)

  if (error) {
    if (error.code !== 'PGRST116') throw error
  }

  _today.value = today
  _pods.value = data || []
  _lastFetchedAt = Date.now()
}

async function loadPODs() {
  // Skip re-fetch if we already have data for today (either rows or confirmed-empty).
  if (_today.value === getTodayIso() && _lastFetchedAt) {
    return
  }
  if (!_podPromise) {
    _podPromise = fetchPODs().catch(e => {
      _podPromise = null
      throw e
    })
  }
  return _podPromise
}

/** Force a re-fetch (e.g. after midnight when the slate rolls over). */
export function refreshTodaysPOD() {
  _today.value = null
  _pods.value = []
  _podPromise = null
  _lastFetchedAt = null
}

// ── Public API ────────────────────────────────────────────────────

/**
 * Composable. Returns:
 *   - pods: ref<podRow[]>      ALL of today's POD rows (HR + HRR + future markets)
 *   - pod:  ref<podRow|null>   back-compat: the HR POD (or first row if no HR)
 *   - ready: computed<bool>    true once fetch has resolved
 *   - isPOD(playerId): bool    true iff playerId matches ANY of today's PODs
 *   - isPODGame(gameId): bool  true iff gameId matches ANY of today's PODs
 *
 * Auto-refresh: tab focus after >5 min triggers a re-fetch.
 */
export function useTodaysPOD() {
  const loadError = ref(null)

  loadPODs().catch(e => {
    console.error('[useTodaysPOD] failed:', e)
    loadError.value = e.message || 'POD fetch failed'
  })

  if (typeof document !== 'undefined') {
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        const ageMs = Date.now() - (_lastFetchedAt || 0)
        if (ageMs > 5 * 60 * 1000) {
          refreshTodaysPOD()
          loadPODs().catch(() => {})
        }
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
  }

  const ready = computed(() => _lastFetchedAt != null)

  // Back-compat: callers that used `pod` get the HR pod by preference,
  // or the first row if HR is missing.
  const pod = computed(() => {
    const list = _pods.value || []
    if (!list.length) return null
    const hr = list.find(p => (p.market_class || 'hr') === 'hr')
    return hr || list[0]
  })

  function isPOD(playerId) {
    if (playerId == null) return false
    const list = _pods.value || []
    if (!list.length) return false
    const pid = Number(playerId)
    return list.some(p => Number(p.player_id) === pid)
  }

  /** True when the given game_id hosts ANY of today's PODs. */
  function isPODGame(gameId) {
    if (gameId == null) return false
    const list = _pods.value || []
    if (!list.length) return false
    const gid = Number(gameId)
    return list.some(p => Number(p.game_id) === gid)
  }

  return {
    pods: _pods,
    pod,
    ready,
    isPOD,
    isPODGame,
    loadError,
  }
}
