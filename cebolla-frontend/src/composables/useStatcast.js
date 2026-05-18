/**
 * useStatcast.js — Composable for fetching Statcast stats by window.
 *
 * Phase 7 (FanGraphs replication). Fetches batter_stats and pitcher_stats rows
 * for any of the rolling windows: 'season' | 'l30' | 'l14' | 'l7'.
 *
 * Caches by (window, playerIdSet) so toggling between windows is instant
 * after first load. Re-fetches if the player set changes.
 */

import { ref, computed, watch } from 'vue'
import { supabase } from '../supabase.js'

/**
 * For batter Statcast (vs_hand = 'A' for rolling windows; season can be L/R/A).
 *
 * Usage:
 *   const { stats, loading, error, setWindow } = useStatcastBatters(playerIds, 'l14')
 *
 * Returns reactive `stats` keyed by player_id:
 *   { 12345: { pa, hr, barrel_pct, hard_hit_pct, xba, xslg, xwoba, ... }, ... }
 */
export function useStatcastBatters(playerIdsRef, initialWindow = 'l14') {
  const windowType = ref(initialWindow)
  const stats = ref({})
  const loading = ref(false)
  const error = ref(null)

  // Cache by `${windowType}|${sortedPlayerIds.join(',')}`
  const cache = new Map()

  async function fetchStats() {
    const ids = playerIdsRef.value
    if (!ids || ids.length === 0) {
      stats.value = {}
      return
    }

    const sortedIds = [...ids].sort((a, b) => a - b)
    const cacheKey = `${windowType.value}|${sortedIds.join(',')}`

    if (cache.has(cacheKey)) {
      stats.value = cache.get(cacheKey)
      return
    }

    loading.value = true
    error.value = null

    try {
      // For rolling windows, only 'A' (all) rows. Season can have L/R/A; we
      // grab 'A' to match the typical view.
      const { data, error: err } = await supabase
        .from('batter_stats')
        .select('batter_id, pa, bbe, hr, hits, avg, slg, hr_per_pa, hit_per_pa, ' +
                'barrel_pct, hard_hit_pct, sweet_spot_pct, xba, xslg, xwoba, ' +
                'ev_avg, ev_max, la_avg, window_start, window_end')
        .in('batter_id', sortedIds)
        .eq('season', new Date().getFullYear())
        .eq('window_type', windowType.value)
        .eq('vs_hand', 'A')

      if (err) throw err

      const map = {}
      for (const row of data || []) {
        map[row.batter_id] = row
      }

      cache.set(cacheKey, map)
      stats.value = map
    } catch (e) {
      console.error('[useStatcastBatters] fetch failed:', e)
      error.value = e.message || 'fetch failed'
      stats.value = {}
    } finally {
      loading.value = false
    }
  }

  function setWindow(newWindow) {
    if (newWindow === windowType.value) return
    windowType.value = newWindow
  }

  // Refetch whenever windowType or player list changes
  watch([windowType, () => playerIdsRef.value], fetchStats, { immediate: true })

  return {
    stats,
    loading,
    error,
    windowType: computed(() => windowType.value),
    setWindow,
  }
}

/**
 * For a single pitcher's allowed-Statcast for a window.
 *
 * Usage:
 *   const { stats, loading, setWindow } = useStatcastPitcher(pitcherIdRef, 'l14')
 *
 * Returns reactive `stats` (single object or null):
 *   { barrel_pct, hard_hit_pct, xba, xslg, xwoba, ... } or null
 */
export function useStatcastPitcher(pitcherIdRef, initialWindow = 'l14') {
  const windowType = ref(initialWindow)
  const stats = ref(null)
  const loading = ref(false)
  const error = ref(null)

  const cache = new Map() // key: `${windowType}|${pitcherId}`

  async function fetchStats() {
    const pitcherId = pitcherIdRef.value
    if (!pitcherId) {
      stats.value = null
      return
    }

    const cacheKey = `${windowType.value}|${pitcherId}`
    if (cache.has(cacheKey)) {
      stats.value = cache.get(cacheKey)
      return
    }

    loading.value = true
    error.value = null

    try {
      const { data, error: err } = await supabase
        .from('pitcher_stats')
        .select('pitcher_id, bbe, barrel_pct, hard_hit_pct, sweet_spot_pct, ' +
                'xba, xslg, xwoba, ev_avg, ev_max, window_start, window_end')
        .eq('pitcher_id', pitcherId)
        .eq('season', new Date().getFullYear())
        .eq('window_type', windowType.value)
        .maybeSingle()

      if (err) throw err

      cache.set(cacheKey, data || null)
      stats.value = data || null
    } catch (e) {
      console.error('[useStatcastPitcher] fetch failed:', e)
      error.value = e.message || 'fetch failed'
      stats.value = null
    } finally {
      loading.value = false
    }
  }

  function setWindow(newWindow) {
    if (newWindow === windowType.value) return
    windowType.value = newWindow
  }

  watch([windowType, () => pitcherIdRef.value], fetchStats, { immediate: true })

  return {
    stats,
    loading,
    error,
    windowType: computed(() => windowType.value),
    setWindow,
  }
}
