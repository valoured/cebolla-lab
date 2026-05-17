/**
 * MLB image URL helpers.
 *
 * MLB hosts team logos and player headshots at predictable URLs on
 * mlbstatic.com. No API call, no auth, no rate limits — just keyed lookups.
 *
 * Logos: keyed by team `mlb_id` (the official MLB org ID, NOT our internal id)
 * Headshots: keyed by player `mlbam_id` (their MLB Advanced Media player ID)
 *
 * Both are returned at modest size; we apply CSS filters for the
 * "lab notebook" monochrome-tinted aesthetic in the components.
 */

const LOGO_BASE     = 'https://www.mlbstatic.com/team-logos'
const HEADSHOT_BASE = 'https://img.mlbstatic.com/mlb-photos/image/upload'

export function teamLogoUrl(mlbTeamId, { variant = 'primary' } = {}) {
  if (!mlbTeamId) return null
  if (variant === 'cap') return `${LOGO_BASE}/${mlbTeamId}-cap-on-dark.svg`
  return `${LOGO_BASE}/${mlbTeamId}.svg`
}

export function playerHeadshotUrl(mlbamId, { size = 60 } = {}) {
  if (!mlbamId) return null
  return `${HEADSHOT_BASE}/d_people:generic:headshot:67:current.png/w_${size},q_auto:best/v1/people/${mlbamId}/headshot/67/current`
}

export function hideOnError(e) {
  if (e?.target) e.target.style.display = 'none'
}
