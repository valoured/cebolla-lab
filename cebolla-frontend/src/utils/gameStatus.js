/**
 * gameStatus.js — shared classification for MLB game status strings.
 *
 * Backend (`pull_scores`) writes `status` strings from the MLB live feed.
 * That feed uses many variants ("In Progress", "Manager Challenge",
 * "Delayed Start: Rain", "Game Over", "Postponed", etc). This util
 * normalises them into three buckets the UI cares about:
 *   - 'final'   → game is finished or won't happen (terminal)
 *   - 'live'    → game in progress, including delays / replay reviews
 *   - 'pregame' → scheduled / pre-game / warmup
 *   - 'unknown' → empty/null/anything else
 *
 * Substring matching handles arbitrary suffixes like "Delayed: Rain".
 * Terminal keywords are checked first so "Postponed" doesn't trip the
 * "delayed" entry in LIVE_KEYWORDS.
 *
 * Mirrored from the backend classifier to keep the UI and the grading
 * logic in lockstep. Both CardsView and CardBlock used to duplicate
 * this logic; now they share this module.
 */

const LIVE_KEYWORDS = [
  'in progress', 'manager challenge', 'umpire review',
  'replay', 'instant replay',
  'delayed', 'suspended',
]
const TERMINAL_KEYWORDS = [
  'final', 'game over', 'completed', 'postponed',
  'cancelled', 'canceled', 'forfeit',
]
const PREGAME_KEYWORDS = [
  'scheduled', 'pre-game', 'pregame', 'warmup', 'status unknown',
]

export function classifyGameStatus(rawStatus) {
  const s = (rawStatus || '').toLowerCase().trim()
  if (!s) return 'unknown'
  if (TERMINAL_KEYWORDS.some(k => s.includes(k))) return 'final'
  if (LIVE_KEYWORDS.some(k => s.includes(k)))     return 'live'
  if (PREGAME_KEYWORDS.some(k => s.includes(k)))  return 'pregame'
  return 'unknown'
}

/**
 * Is the game live RIGHT NOW based on `gamesById[gameId]`?
 *
 * Falls back to a 5min-after-start-to-4hr-after-start window when the
 * status string is unhelpful (pregame/unknown but the game has clearly
 * started). 4hr cap avoids treating a 9hr-old game with a stale status
 * as still live.
 */
export function isGameLive(gamesById, gameId) {
  const g = gamesById?.[gameId]
  if (!g) return false
  const klass = classifyGameStatus(g.status)
  if (klass === 'live') return true
  if (klass === 'pregame' || klass === 'unknown') {
    if (g.game_time_utc) {
      const start = new Date(g.game_time_utc).getTime()
      const now = Date.now()
      if (now > start + 5 * 60_000 && now < start + 4 * 60 * 60_000) return true
    }
  }
  return false
}

export function isGameFinal(gamesById, gameId) {
  const g = gamesById?.[gameId]
  if (!g) return false
  return classifyGameStatus(g.status) === 'final'
}
