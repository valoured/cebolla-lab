// composables/useShadowLab.js
// Shadow Lab data layer — reads picks_v2_enriched (anon view).
//
// STATIC by design: no realtime subscription, no polling. The only refresh
// triggers are refresh() on mount and setCalibWindow() from the window toggle.
// Single-user research surface, so no auth filtering.

import { ref, computed } from 'vue'
import { supabase } from '../supabase'

// Bucket display order for the calibration rollup (longshot_unrated excluded).
export const EDGE_STATUS_ORDER = ['strong_back', 'lean_back', 'flat', 'lean_fade', 'strong_fade']

// Window → lookback in days. null = no lower bound (All).
const WINDOW_DAYS = { today: 0, l7: 7, l30: 30, all: null }

// ── Module-level singleton state (mirrors useBetLog) ──
const _today = ref([])          // today's enriched picks → Picks table
const _calibRows = ref([])      // windowed enriched picks → Calibration rollup
const _loading = ref(false)
const _error = ref(null)
const _initialized = ref(false)
const _calibWindow = ref('today')

// ET-relative slate date (matches the backend's (utc - 4h) convention).
function etTodayIso() {
  const d = new Date(Date.now() - 4 * 3600 * 1000)
  return d.toISOString().slice(0, 10)
}

// Lower-bound date for a window, or null for All.
function windowFloorIso(window) {
  const days = WINDOW_DAYS[window]
  if (days == null) return null
  const d = new Date(Date.now() - 4 * 3600 * 1000 - days * 86400 * 1000)
  return d.toISOString().slice(0, 10)
}

// hr / (hr + no_hr) over a set of rows; null when no settled picks.
function hitRate(rows) {
  const hr = rows.filter(r => r.outcome_status === 'hr').length
  const no = rows.filter(r => r.outcome_status === 'no_hr').length
  const denom = hr + no
  return denom ? hr / denom : null
}

function avg(nums) {
  const v = nums.filter(n => n != null)
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null
}

// ── Fetchers ──
async function fetchToday(dateIso) {
  _loading.value = true
  _error.value = null
  try {
    const { data, error } = await supabase
      .from('picks_v2_enriched')
      .select('*')
      .eq('pick_date', dateIso || etTodayIso())
      .order('edge_pct', { ascending: false, nullsFirst: false })
    if (error) throw error
    _today.value = data || []
  } catch (e) {
    _error.value = e
    console.error('[shadow] fetchToday failed', e)
  } finally {
    _loading.value = false
  }
}

async function fetchWindow(window) {
  try {
    let q = supabase.from('picks_v2_enriched').select('*')
    const floor = windowFloorIso(window)
    if (floor) q = q.gte('pick_date', floor)
    const { data, error } = await q
    if (error) throw error
    _calibRows.value = data || []
  } catch (e) {
    _error.value = e
    console.error('[shadow] fetchWindow failed', e)
  }
}

// Mount refresh: today's picks + the current calibration window.
async function refresh() {
  await Promise.all([fetchToday(), fetchWindow(_calibWindow.value)])
  _initialized.value = true
}

// Window toggle — re-fetches the calibration set only; Picks table stays today.
async function setCalibWindow(window) {
  _calibWindow.value = window
  await fetchWindow(window)
}

// ── Client-side calibration aggregation (lock #4) ──
// Group _calibRows by edge_status (EDGE_STATUS_ORDER), excluding
// longshot_unrated. hit_rate denominator counts only settled picks
// (outcome_status in hr|no_hr) — did_not_play / game_void / pending excluded.
const calibrationByEdgeStatus = computed(() => {
  return EDGE_STATUS_ORDER.map(status => {
    const rows = _calibRows.value.filter(r => r.edge_status === status)
    const settled = rows.filter(r => r.outcome_status === 'hr' || r.outcome_status === 'no_hr')
    return {
      edge_status: status,
      n: rows.length,
      settled: settled.length,
      hr: rows.filter(r => r.outcome_status === 'hr').length,
      hit_rate: hitRate(rows),
      avg_model_prob: avg(rows.map(r => Number(r.model_prob_per_game))),
      avg_edge: avg(rows.map(r => r.edge_pct != null ? Number(r.edge_pct) : null)),
    }
  }).filter(b => b.n > 0)
})

export function useShadowLab() {
  return {
    today: _today,
    calibRows: _calibRows,
    loading: _loading,
    error: _error,
    initialized: _initialized,
    calibWindow: _calibWindow,
    fetchToday,
    fetchWindow,
    refresh,
    setCalibWindow,
    calibrationByEdgeStatus,
    EDGE_STATUS_ORDER,
  }
}
