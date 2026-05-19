/**
 * useContactPool.js — Composable that fetches the league-wide pool of
 * qualified batters and exposes scoring helpers.
 *
 * Why a "league-wide" pool instead of "tonight's slate" pool?
 *
 *   The contact score is meant to answer "how good is this batter's contact
 *   right now, in absolute terms." When the percentile pool is just tonight's
 *   slate, the meaning of "78" depends on whatever 200 batters happen to be
 *   playing tonight. With a league-wide pool, "78" always means "top 22% of
 *   all qualified MLB batters this season." Stable, comparable across days.
 *
 *   For card-building, this is more useful: a 90 means elite contact
 *   regardless of who's in the slate. A 30 means weak contact, full stop.
 *
 * What "qualified" means:
 *   - season = current MLB season
 *   - window_type = 'l14'  (the L14 score uses L14 percentiles)
 *   - vs_hand = 'A'        (no platoon split)
 *   - pa >= MIN_PA (20)    (sample floor for honest rate stats)
 *
 * Single fetch, module-level cache so all BatterTable instances share it.
 * Re-fetches once per session unless explicitly refreshed.
 */

import { ref, computed } from 'vue'
import { supabase } from '../supabase.js'
import {
  buildContactPools,
  contactScore,
  contactTrend,
  MIN_PA,
} from './useContactScore.js'

const CURRENT_SEASON = new Date().getFullYear()

// ── Module-level cache ─────────────────────────────────────────────
// All consumers share the same pool. Initialized lazily by first call to
// loadPool(). Subsequent calls reuse the cached pool until refresh().
let _poolPromise = null
let _lastFetchedAt = null         // ms timestamp of last successful fetch
const _l14Pool = ref(null)        // { barrel_pct: [...], hard_hit_pct: [...], xslg: [...] }
const _seasonRowsById = ref(new Map())  // for season scores in trend calc

async function fetchPool() {
  // L14 — used for the percentile pool AND for scoring.
  // Filter at the DB level to qualified batters (PA >= MIN_PA) to keep the
  // payload small.
  const [{ data: l14Data, error: l14Err }, { data: seasonData, error: seasonErr }] =
    await Promise.all([
      supabase
        .from('batter_stats')
        .select('batter_id, pa, barrel_pct, hard_hit_pct, xslg')
        .eq('season', CURRENT_SEASON)
        .eq('window_type', 'l14')
        .eq('vs_hand', 'A')
        .gte('pa', MIN_PA),
      supabase
        .from('batter_stats')
        .select('batter_id, pa, barrel_pct, hard_hit_pct, xslg')
        .eq('season', CURRENT_SEASON)
        .eq('window_type', 'season')
        .eq('vs_hand', 'A'),
    ])

  if (l14Err) throw l14Err
  if (seasonErr) throw seasonErr

  // Build pool from qualified L14 rows
  _l14Pool.value = buildContactPools(l14Data || [])

  // Index season rows for trend lookups
  const seasonMap = new Map()
  for (const row of (seasonData || [])) {
    if (row && row.batter_id != null) seasonMap.set(row.batter_id, row)
  }
  _seasonRowsById.value = seasonMap
  _lastFetchedAt = Date.now()
}

async function loadPool() {
  if (_l14Pool.value) return  // already loaded
  if (!_poolPromise) {
    _poolPromise = fetchPool().catch(e => {
      _poolPromise = null  // allow retry on failure
      throw e
    })
  }
  return _poolPromise
}

/**
 * Force a re-fetch (e.g. after midnight when L14 windows shift).
 */
export function refreshContactPool() {
  _l14Pool.value = null
  _seasonRowsById.value = new Map()
  _poolPromise = null
}

// ── Public API ────────────────────────────────────────────────────

/**
 * Composable. Call at component setup; returns reactive scoring helpers.
 *
 * Usage:
 *   const { ready, loadError, getSnapshot } = useContactPool()
 *
 *   // For a single batter (l14 stats object from useStatcastBatters):
 *   const { score, trend } = getSnapshot(batterId, l14Stats)
 *
 * Auto-refresh policy:
 *   - First call fetches the pool (~500 rows).
 *   - Subsequent useContactPool() instances in the same session reuse the
 *     module-level cache (instant).
 *   - When the user returns to the tab after being away (visibilitychange),
 *     the pool is invalidated and re-fetched on next access. This keeps
 *     scores fresh through the day as pull_savant.py and compute_projections
 *     update the underlying batter_stats table.
 */
export function useContactPool() {
  const loadError = ref(null)
  const ready = ref(false)

  // Kick off the load if needed
  loadPool()
    .then(() => { ready.value = true })
    .catch(e => {
      console.error('[useContactPool] failed to load pool:', e)
      loadError.value = e.message || 'pool load failed'
    })

  // Refresh on tab focus — handles the case where the user leaves the app
  // open for hours and the underlying batter_stats refreshes (e.g. after
  // the 2:13 AM ET pull_savant run). Cheap: ~500-row re-fetch, only when
  // user actively returns.
  //
  // We attach the listener once per useContactPool call. Multiple components
  // calling useContactPool will each attach a listener but they all share
  // the same module-level _poolPromise, so only ONE re-fetch happens per
  // visibility event regardless of listener count.
  if (typeof document !== 'undefined') {
    const onVisibility = () => {
      if (document.visibilityState === 'visible' && _l14Pool.value) {
        // Only re-fetch if data is at least 5 minutes old. Avoids thrashing
        // when user is tabbing between this and another tab rapidly.
        const ageMs = Date.now() - (_lastFetchedAt || 0)
        if (ageMs > 5 * 60 * 1000) {
          refreshContactPool()
          loadPool().then(() => { ready.value = true }).catch(() => {})
        }
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
  }

  // Watch for pool changes (rare — only on initial load or refresh)
  // Use a computed that depends on _l14Pool so consumers re-render once it's
  // ready.
  const poolReady = computed(() => _l14Pool.value != null)

  /**
   * Given a batter's L14 stats (the active row from useStatcastBatters with
   * window 'l14'), return { score, trend }. Both may be null:
   *   - score null if PA < MIN_PA or no qualifying components
   *   - trend null if either L14 or season score is null
   *
   * Does NOT modify input. Pure read.
   */
  function getSnapshot(batterId, l14Stats) {
    if (!poolReady.value || !_l14Pool.value) {
      return { score: null, trend: null }
    }
    if (!l14Stats) return { score: null, trend: null }

    // L14 score against league pool
    const l14Score = contactScore(
      {
        pa: l14Stats.pa,
        barrel_pct: l14Stats.barrel_pct,
        hard_hit_pct: l14Stats.hard_hit_pct,
        xslg: l14Stats.xslg,
      },
      _l14Pool.value,
    )

    // Season score (for trend) — score the same batter's season stats
    // against the SAME L14 pool so we're comparing apples to apples on the
    // same scale. (Otherwise the trend would mix L14-pool percentiles with
    // season-pool percentiles and be meaningless.)
    const seasonRow = _seasonRowsById.value.get(batterId)
    const seasonScore = seasonRow
      ? contactScore(
          {
            pa: seasonRow.pa,
            barrel_pct: seasonRow.barrel_pct,
            hard_hit_pct: seasonRow.hard_hit_pct,
            xslg: seasonRow.xslg,
          },
          _l14Pool.value,
        )
      : null

    return {
      score: l14Score,
      trend: contactTrend(l14Score, seasonScore),
    }
  }

  return {
    ready: poolReady,
    loadError,
    getSnapshot,
  }
}
