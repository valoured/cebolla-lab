<script setup>
/**
 * WindGauge.vue — Field-relative wind direction gauge.
 *
 * Renders a ~44px SVG showing:
 *   - A baseball-diamond outline (home plate at bottom, CF at top)
 *   - An arrow showing wind direction RELATIVE to the field's orientation
 *   - Wind speed as an mph number beside the gauge
 *
 * Why field-relative: a 10mph wind blowing "north" means nothing to a
 * batter — it matters whether it's blowing OUT to CF or IN from CF. Each
 * stadium points a different compass direction, so we rotate the wind
 * vector against the home-plate-to-CF bearing.
 *
 * Color coding:
 *   - Out (helping HRs):  signal red glow
 *   - Crosswind (neutral): muted gray
 *   - In (suppressing):   cold blue
 *   - Dome / no wind:     dim placeholder, no arrow
 *
 * Props:
 *   - teamAbbrev: home team abbrev — used to look up CF bearing
 *   - windDirDeg: Open-Meteo wind direction in degrees (direction FROM which wind blows)
 *   - windMph:    wind speed in mph
 *   - isDome:     true if stadium is dome / wind irrelevant
 */

import { computed } from 'vue'

const props = defineProps({
  teamAbbrev: { type: String,  default: null },
  windDirDeg: { type: [Number, String], default: null },
  windMph:    { type: [Number, String], default: null },
  isDome:     { type: Boolean, default: false },
})

// Home-plate-to-CF compass bearing per team. Mirrored from
// pull_weather.py's CF_BEARING_BY_TEAM_ABBREV. Keep in sync if updated.
// Approximate; refine with measured values if needed.
const CF_BEARING_BY_TEAM = {
  ARI: 23,  ATL: 50,  BAL: 38,  BOS: 45,  CHC: 30,
  CWS:130,  CIN: 35,  CLE:  0,  COL:  0,  DET:145,
  HOU:348,  KC:  45,  LAA: 60,  LAD: 25,  MIA: 40,
  MIL:135,  MIN: 90,  NYM: 25,  NYY: 75,  ATH: 60,
  PHI: 15,  PIT:117,  SD:   0,  SF:  90,  SEA: 45,
  STL: 60,  TB:  45,  TEX:  0,  TOR:  0,  WSH: 30,
}

// Compute the field-relative wind angle in degrees.
// 0   = blowing straight OUT to CF (helping HRs)
// 180 = blowing straight IN from CF (suppressing)
// 90  = crosswind from 1B → 3B side
// 270 = crosswind from 3B → 1B side
//
// Open-Meteo gives wind_from_deg (where the wind originates).
// "blowing toward" = (from + 180) % 360.
// Then subtract the CF bearing to put it in field-frame coordinates.
const fieldAngleDeg = computed(() => {
  const wd = Number(props.windDirDeg)
  if (!Number.isFinite(wd)) return null
  const cfBearing = CF_BEARING_BY_TEAM[props.teamAbbrev] ?? null
  if (cfBearing == null) return null
  const blowingToward = (wd + 180) % 360
  return ((blowingToward - cfBearing) + 360) % 360
})

// Tone the arrow based on whether wind helps or hurts HRs.
// Mirrors the categorization in pull_weather.py's wind_relative_to_cf.
const windTone = computed(() => {
  if (props.isDome) return 'dome'
  const a = fieldAngleDeg.value
  if (a == null) return 'unknown'
  // Symmetric: care about deflection from 0 (out) or 180 (in)
  const fromOut = Math.min(a, 360 - a)        // 0 means straight out
  const fromIn  = Math.min(Math.abs(a - 180), 360 - Math.abs(a - 180))  // 0 means straight in
  if (fromOut <= 30) return 'out-strong'
  if (fromOut <= 60) return 'out-mild'
  if (fromIn  <= 30) return 'in-strong'
  if (fromIn  <= 60) return 'in-mild'
  return 'cross'
})

const arrowColor = computed(() => {
  switch (windTone.value) {
    case 'out-strong': return '#FF2A2A'
    case 'out-mild':   return 'rgba(255, 42, 42, 0.65)'
    case 'in-strong':  return 'rgba(95, 165, 255, 0.95)'
    case 'in-mild':    return 'rgba(95, 165, 255, 0.65)'
    case 'cross':      return 'rgba(255, 255, 255, 0.45)'
    case 'dome':       return 'rgba(255, 255, 255, 0.25)'
    default:           return 'rgba(255, 255, 255, 0.30)'
  }
})

const arrowGlow = computed(() => {
  if (windTone.value === 'out-strong') return 'drop-shadow(0 0 3px rgba(255, 42, 42, 0.55))'
  if (windTone.value === 'in-strong')  return 'drop-shadow(0 0 3px rgba(95, 165, 255, 0.45))'
  return 'none'
})

// Show the arrow only when we have a meaningful angle AND it's not a dome.
const showArrow = computed(() => {
  return !props.isDome && fieldAngleDeg.value != null && Number(props.windMph) > 0.5
})

// SVG arrow rotation. The arrow points "up" by default (toward CF at the
// top of the diamond), so we just apply the field angle as rotation.
const arrowRotation = computed(() => fieldAngleDeg.value || 0)

// Display the mph number with sensible rounding
const mphDisplay = computed(() => {
  if (props.isDome) return 'dome'
  const v = Number(props.windMph)
  if (!Number.isFinite(v)) return '—'
  return `${Math.round(v)}`
})

const mphSuffix = computed(() => {
  if (props.isDome) return ''
  return 'mph'
})
</script>

<template>
  <div class="wind-gauge-wrap">
    <!-- Gauge: 44x44 SVG.
         Diamond outline drawn with home plate at bottom (cy=34) and
         CF at the top (cy=10). Arrow rotates from the center. -->
    <svg
      class="wind-gauge"
      viewBox="0 0 44 44"
      xmlns="http://www.w3.org/2000/svg"
      :class="{ 'is-dome': isDome }"
    >
      <!-- Diamond (rotated square) — home at bottom, CF at top -->
      <polygon
        points="22,7 37,22 22,37 7,22"
        fill="none"
        :stroke="isDome ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.20)'"
        stroke-width="1"
      />
      <!-- Home plate marker (small dot at bottom vertex) -->
      <circle
        cx="22"
        cy="36"
        r="1.4"
        :fill="isDome ? 'rgba(255,255,255,0.20)' : 'rgba(255,255,255,0.45)'"
      />
      <!-- CF marker (small line at top vertex) -->
      <line
        x1="20"
        y1="7"
        x2="24"
        y2="7"
        :stroke="isDome ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.35)'"
        stroke-width="1"
        stroke-linecap="round"
      />

      <!-- Wind arrow.
           Drawn pointing UP (toward CF) by default at the center, then
           rotated to match the field-relative angle. 0deg = straight out to CF. -->
      <g
        v-if="showArrow"
        :transform="`rotate(${arrowRotation} 22 22)`"
        :style="{ filter: arrowGlow }"
      >
        <!-- Arrow shaft -->
        <line
          x1="22"
          y1="30"
          x2="22"
          y2="14"
          :stroke="arrowColor"
          stroke-width="1.8"
          stroke-linecap="round"
        />
        <!-- Arrow head -->
        <polyline
          points="18,18 22,13 26,18"
          fill="none"
          :stroke="arrowColor"
          stroke-width="1.8"
          stroke-linejoin="round"
          stroke-linecap="round"
        />
      </g>

      <!-- Dome marker: small "x" through the diamond -->
      <g v-else-if="isDome" stroke="rgba(255,255,255,0.25)" stroke-width="1" stroke-linecap="round">
        <line x1="14" y1="14" x2="30" y2="30" />
        <line x1="30" y1="14" x2="14" y2="30" />
      </g>
    </svg>

    <!-- mph readout next to gauge -->
    <div class="wind-mph">
      <span class="display-num wind-mph-num" :class="`tone-${windTone}`">{{ mphDisplay }}</span>
      <span v-if="mphSuffix" class="wind-mph-unit">{{ mphSuffix }}</span>
    </div>
  </div>
</template>

<style scoped>
.wind-gauge-wrap {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  line-height: 1;
}

.wind-gauge {
  width: 36px;
  height: 36px;
  display: block;
  flex-shrink: 0;
}
@media (min-width: 640px) {
  .wind-gauge {
    width: 44px;
    height: 44px;
  }
}
.wind-gauge.is-dome {
  opacity: 0.55;
}

.wind-mph {
  display: inline-flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.1;
}
.wind-mph-num {
  font-size: 18px;
  font-weight: 600;
}
@media (max-width: 640px) {
  .wind-mph-num {
    font-size: 16px;
  }
}
.wind-mph-unit {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  opacity: 0.55;
  margin-top: 1px;
}

/* Tone colors for the mph number — match arrow colors so the readout
   reinforces the visual direction signal. */
.tone-out-strong { color: #FF2A2A; }
.tone-out-mild   { color: rgba(255, 42, 42, 0.80); }
.tone-in-strong  { color: rgba(95, 165, 255, 0.95); }
.tone-in-mild    { color: rgba(95, 165, 255, 0.75); }
.tone-cross      { color: rgba(255, 255, 255, 0.65); }
.tone-dome       { color: rgba(255, 255, 255, 0.40); font-size: 13px; }
.tone-unknown    { color: rgba(255, 255, 255, 0.40); }
</style>
