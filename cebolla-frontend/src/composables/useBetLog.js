// composables/useBetLog.js
// Fetch, insert, and update bet_log entries.
// Single-user app, so no auth filtering.

import { ref, computed } from 'vue'
import { supabase } from '../supabase'
import { ping } from './useRealtimePulse.js'

const _bets = ref([])
const _loading = ref(false)
const _error = ref(null)
const _initialized = ref(false)
let _realtimeChannel = null
let _reloadTimer = null

async function fetchBets({ limit = 200 } = {}) {
  _loading.value = true
  _error.value = null
  try {
    const { data, error } = await supabase
      .from('bet_log_enriched')
      .select('*')
      .order('placed_at', { ascending: false })
      .limit(limit)
    if (error) throw error
    _bets.value = data || []
    _initialized.value = true
  } catch (e) {
    _error.value = e
    console.error('[bet_log] fetch failed', e)
  } finally {
    _loading.value = false
  }
}

function scheduleReload() {
  if (_reloadTimer) clearTimeout(_reloadTimer)
  _reloadTimer = setTimeout(() => {
    fetchBets()
    ping()
  }, 1500)
}

function ensureRealtimeSubscription() {
  if (_realtimeChannel) return
  _realtimeChannel = supabase
    .channel('bet-log-changes')
    .on('postgres_changes',
        { event: '*', schema: 'public', table: 'bet_log' },
        () => scheduleReload())
    .subscribe()
}

async function insertBet(row) {
  const payload = {
    placed_at: new Date().toISOString(),
    result: 'pending',
    book: 'draftkings',
    ...row,
  }
  const { data, error } = await supabase
    .from('bet_log')
    .insert(payload)
    .select()
    .single()
  if (error) throw error
  await fetchBets()
  return data
}

async function updateBet(id, patch) {
  const { data, error } = await supabase
    .from('bet_log')
    .update(patch)
    .eq('id', id)
    .select()
    .single()
  if (error) throw error
  await fetchBets()
  return data
}

async function deleteBet(id) {
  const { error } = await supabase.from('bet_log').delete().eq('id', id)
  if (error) throw error
  await fetchBets()
}

async function fetchRoiByEdge() {
  const { data, error } = await supabase.from('roi_by_edge_bucket').select('*')
  if (error) {
    console.error('[bet_log] roi by edge failed', error)
    return []
  }
  return data || []
}

async function fetchRoiByModel() {
  const { data, error } = await supabase.from('roi_by_model_version').select('*')
  if (error) {
    console.error('[bet_log] roi by model failed', error)
    return []
  }
  return data || []
}

// ─── Derived stats ───
const totalStaked = computed(() =>
  _bets.value
    .filter(b => b.result && b.result !== 'pending' && b.result !== 'void')
    .reduce((sum, b) => sum + (parseFloat(b.stake) || 0), 0)
)

const totalPnl = computed(() =>
  _bets.value
    .filter(b => b.pnl !== null && b.pnl !== undefined)
    .reduce((sum, b) => sum + (parseFloat(b.pnl) || 0), 0)
)

const pendingCount = computed(() =>
  _bets.value.filter(b => b.result === 'pending').length
)

const settledCount = computed(() =>
  _bets.value.filter(b => ['win', 'loss', 'push', 'void'].includes(b.result)).length
)

const overallRoi = computed(() => {
  const staked = totalStaked.value
  if (!staked) return null
  return totalPnl.value / staked
})

const winRate = computed(() => {
  const wins   = _bets.value.filter(b => b.result === 'win').length
  const losses = _bets.value.filter(b => b.result === 'loss').length
  const denom  = wins + losses
  return denom ? wins / denom : null
})

export function useBetLog() {
  // Set up realtime subscription on first use (idempotent)
  ensureRealtimeSubscription()

  return {
    bets: _bets,
    loading: _loading,
    error: _error,
    initialized: _initialized,
    fetchBets,
    insertBet,
    updateBet,
    deleteBet,
    fetchRoiByEdge,
    fetchRoiByModel,
    totalStaked,
    totalPnl,
    pendingCount,
    settledCount,
    overallRoi,
    winRate,
  }
}
