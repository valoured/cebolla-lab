/**
 * useFavorites.js — localStorage-backed watchlist for players and teams.
 *
 * Single-user app, no auth, no backend. Favorites live in the user's
 * browser via localStorage and survive reloads on that device.
 *
 * Storage format:
 *   key: 'cebolla.favorites.v1'
 *   value: JSON string of
 *     {
 *       players: { [playerId]: { id, name, team_abbrev, position, addedAt } },
 *       teams:   { [teamId]:   { id, abbrev, name, addedAt } }
 *     }
 *
 * The composable returns a singleton-style API — same module-level
 * refs are shared across all callers, so toggling on one page reflects
 * immediately everywhere else without a refresh.
 *
 * Cross-tab sync: a 'storage' event listener picks up changes made in
 * other tabs and updates the in-memory state.
 */

import { ref, computed, watch } from 'vue'

const STORAGE_KEY = 'cebolla.favorites.v1'

// Module-level state (shared across all useFavorites() consumers)
const players = ref({})
const teams = ref({})
let loaded = false

function loadFromStorage() {
  if (typeof window === 'undefined') return
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) {
      players.value = {}
      teams.value = {}
      return
    }
    const parsed = JSON.parse(raw)
    players.value = parsed?.players && typeof parsed.players === 'object' ? parsed.players : {}
    teams.value   = parsed?.teams   && typeof parsed.teams   === 'object' ? parsed.teams   : {}
  } catch (e) {
    console.warn('[useFavorites] failed to read storage, resetting:', e)
    players.value = {}
    teams.value = {}
  }
}

function saveToStorage() {
  if (typeof window === 'undefined') return
  try {
    const payload = JSON.stringify({
      players: players.value,
      teams: teams.value,
    })
    window.localStorage.setItem(STORAGE_KEY, payload)
  } catch (e) {
    // Storage might be unavailable (private mode, quota). Best-effort.
    console.warn('[useFavorites] failed to write storage:', e)
  }
}

function ensureInit() {
  if (loaded) return
  loaded = true
  loadFromStorage()

  // Cross-tab sync
  if (typeof window !== 'undefined') {
    window.addEventListener('storage', (e) => {
      if (e.key === STORAGE_KEY) loadFromStorage()
    })
  }

  // Auto-save on any change
  watch([players, teams], () => saveToStorage(), { deep: true })
}

// ── Public API ─────────────────────────────────────────────────
export function useFavorites() {
  ensureInit()

  function isPlayerFav(id) {
    return !!players.value[String(id)]
  }
  function isTeamFav(id) {
    return !!teams.value[String(id)]
  }

  /**
   * Toggle a player. `playerData` should include at minimum {id, name}.
   * Team abbrev and position are optional but improve the surface display.
   */
  function togglePlayer(playerData) {
    if (!playerData?.id) return
    const key = String(playerData.id)
    const next = { ...players.value }
    if (next[key]) {
      delete next[key]
    } else {
      next[key] = {
        id: playerData.id,
        name: playerData.name || '',
        team_abbrev: playerData.team_abbrev || playerData.team?.abbrev || '',
        position: playerData.position || '',
        is_pitcher: !!playerData.is_pitcher,
        addedAt: Date.now(),
      }
    }
    players.value = next
  }

  function toggleTeam(teamData) {
    if (!teamData?.id) return
    const key = String(teamData.id)
    const next = { ...teams.value }
    if (next[key]) {
      delete next[key]
    } else {
      next[key] = {
        id: teamData.id,
        abbrev: teamData.abbrev || '',
        name: teamData.name || '',
        addedAt: Date.now(),
      }
    }
    teams.value = next
  }

  function clearAll() {
    players.value = {}
    teams.value = {}
  }

  // Sorted lists for display (most recently added first)
  const playerList = computed(() =>
    Object.values(players.value).sort((a, b) => b.addedAt - a.addedAt)
  )
  const teamList = computed(() =>
    Object.values(teams.value).sort((a, b) => b.addedAt - a.addedAt)
  )

  const totalCount = computed(() =>
    Object.keys(players.value).length + Object.keys(teams.value).length
  )

  return {
    // raw maps (rarely needed)
    players,
    teams,
    // lists for rendering
    playerList,
    teamList,
    totalCount,
    // checks
    isPlayerFav,
    isTeamFav,
    // mutations
    togglePlayer,
    toggleTeam,
    clearAll,
  }
}
