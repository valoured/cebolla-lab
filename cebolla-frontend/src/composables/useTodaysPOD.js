/**
 * useTodaysPOD.js — Fetches today's Play of the Day once per session and
 * exposes helpers for "is this player today's POD?" checks across the site.
 *
 * Used by:
 *   - BatterTable / BatterCard (gold badge on player rows)
 *   - GameCard on the slate (small gold pip when the game has the POD)
 *   - PlayerView (header tag when viewing today's POD player)
 *
 * Single shared module-level cache. All consumers see the same answer.
 * Refreshes when the user returns to the tab after being away >5 min.
 *
 * No DB changes — reads from existing pods table.
 */

import { ref, computed } from 'vue'
import { supabase } from '../supabase.js'

// ── Module-level cache ─────────────────────────────────────────────
let _podPromise = null
let _lastFetchedAt = null
const _today = ref(null)              // ISO date string of fetched POD's pod_date
const _pod = ref(null)                // full POD row, or null if no POD today

/** ET-relative today (same convention used in pick_pod.py + frontend) */
function getTodayIso() {
  // Browser doesn't have a clean ET helper without bringing in a date lib.
  // Use UTC-4 offset which is correct for EDT (most of the season).
  // Edge case: brief windows in March/November when EST is UTC-5, the offset
  // is off by an hour — would only matter if the user opens the app between
  // 12 AM and 1 AM ET on the standard-time side. Negligible.
  const now = new Date()
  const etMs = now.getTime() + now.getTimezoneOffset() * 60_000 + (-4 * 60 * 60_000)
  return new Date(etMs).toISOString().slice(0, 10)
}

async function fetchPOD() {
  const today = getTodayIso()
  const { data, error } = await supabase
    .from('pods')
    .select('id, pod_date, game_id, player_id, player_mlbam_id, player_name, team_abbrev, opponent_abbrev, market, status')
    .eq('pod_date', today)
    .limit(1)
    .maybeSingle()

  if (error) {
    // PGRST116 means no row — that's expected on slates where no POD locked.
    // Other errors should bubble up.
    if (error.code !== 'PGRST116') throw error
  }

  _today.value = today
  _pod.value = data || null
  _lastFetchedAt = Date.now()
}

async function loadPOD() {
  if (_pod.value !== null || (_today.value === getTodayIso() && _lastFetchedAt)) {
    // Either we have a POD row, or we previously confirmed there's none today.
    // Either way, no need to re-fetch from cold.
    return
  }
  if (!_podPromise) {
    _podPromise = fetchPOD().catch(e => {
      _podPromise = null
      throw e
    })
  }
  return _podPromise
}

/** Force a re-fetch (e.g. after midnight when the slate rolls over). */
export function refreshTodaysPOD() {
  _today.value = null
  _pod.value = null
  _podPromise = null
  _lastFetchedAt = null
}

// ── Public API ────────────────────────────────────────────────────

/**
 * Composable. Returns:
 *   - pod: ref<podRow | null>  the full POD row for today, or null
 *   - ready: computed<bool>    true once the fetch has resolved (either to a
 *                              POD or to a confirmed-empty state)
 *   - isPOD(playerId): bool   convenience — true iff playerId matches today's POD
 *
 * Auto-refresh: tab focus after >5 min triggers a re-fetch.
 */
export function useTodaysPOD() {
  const loadError = ref(null)

  loadPOD().catch(e => {
    console.error('[useTodaysPOD] failed:', e)
    loadError.value = e.message || 'POD fetch failed'
  })

  if (typeof document !== 'undefined') {
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        const ageMs = Date.now() - (_lastFetchedAt || 0)
        if (ageMs > 5 * 60 * 1000) {
          refreshTodaysPOD()
          loadPOD().catch(() => {})
        }
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
  }

  const ready = computed(() => _lastFetchedAt != null)

  function isPOD(playerId) {
    if (playerId == null) return false
    if (!_pod.value) return false
    return Number(_pod.value.player_id) === Number(playerId)
  }

  /** True when the given game_id is the game hosting today's POD. */
  function isPODGame(gameId) {
    if (gameId == null) return false
    if (!_pod.value) return false
    return Number(_pod.value.game_id) === Number(gameId)
  }

  return {
    pod: _pod,
    ready,
    isPOD,
    isPODGame,
    loadError,
  }
}
