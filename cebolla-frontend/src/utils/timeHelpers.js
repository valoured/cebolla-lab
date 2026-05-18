/**
 * timeHelpers.js — Centralized time formatting for Cebolla Lab.
 *
 * All baseball times are displayed in ET (America/New_York), since:
 *   - MLB schedules in ET
 *   - Our user base is currently East Coast (Florida)
 *   - It avoids surprise for users in other US timezones
 *
 * Internal storage stays UTC (ISO 8601 in `game_time_utc` columns).
 * These helpers convert at display time.
 */

const ET_TIMEZONE = 'America/New_York'

/**
 * Parse a UTC timestamp string and return a Date.
 * Tolerant of trailing 'Z' or '+00:00'.
 */
function parseUtc(utc) {
  if (!utc) return null
  if (utc instanceof Date) return utc
  const cleaned = utc.replace(/\.\d+/, '').replace('Z', '+00:00')
  const d = new Date(cleaned)
  return isNaN(d.getTime()) ? null : d
}

/**
 * "6:40 PM ET" — first pitch time, clean.
 */
export function formatGameTime(utc) {
  const d = parseUtc(utc)
  if (!d) return '—'
  return d.toLocaleTimeString('en-US', {
    timeZone: ET_TIMEZONE,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }) + ' ET'
}

/**
 * "Lineups expected around 3:40 PM ET"
 * MLB teams typically post confirmed lineups ~3 hours before first pitch.
 */
export function formatLineupETA(utc) {
  const d = parseUtc(utc)
  if (!d) return null
  const lineupTime = new Date(d.getTime() - 3 * 60 * 60 * 1000)
  return lineupTime.toLocaleTimeString('en-US', {
    timeZone: ET_TIMEZONE,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }) + ' ET'
}

/**
 * Returns a relative countdown string for a future time.
 *   "in 2h 14m"   (when >= 1h away)
 *   "in 14m"      (when <1h away but >=1m)
 *   "starting now" (when within 1 minute)
 *   "started"     (when in the past, fallback — usually means status field will say "live" or "final")
 */
export function formatCountdown(utc) {
  const d = parseUtc(utc)
  if (!d) return null
  const ms = d.getTime() - Date.now()

  if (ms < -60_000) return 'started'
  if (ms < 60_000)  return 'starting now'

  const totalMinutes = Math.floor(ms / 60_000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60

  if (hours === 0) return `in ${minutes}m`
  return `in ${hours}h ${minutes}m`
}

/**
 * How many minutes until first pitch. Negative = in the past.
 * Useful for deciding when to show countdowns vs static times.
 */
export function minutesUntil(utc) {
  const d = parseUtc(utc)
  if (!d) return null
  return Math.round((d.getTime() - Date.now()) / 60_000)
}

/**
 * Compact short form: "6:40 PM" without the ET suffix (use when context is obvious).
 */
export function formatGameTimeShort(utc) {
  const d = parseUtc(utc)
  if (!d) return '—'
  return d.toLocaleTimeString('en-US', {
    timeZone: ET_TIMEZONE,
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}
