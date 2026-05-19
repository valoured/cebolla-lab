<script setup>
/**
 * WindGauge.vue — Field-relative wind direction gauge.
 *
 * Renders an SVG showing:
 *   - A baseball-diamond outline (home plate at bottom, CF at top)
 *     with labeled corners (CF / LF / RF / H) and outward tick marks
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
 *   - cfBearing:  compass bearing from home plate to CF (degrees, 0-359).
 *                 Comes from teams.home_plate_bearing — the single source
 *                 of truth. If null, gauge can't compute direction and
 *                 falls back to a neutral display with just the mph.
 *   - windDirDeg: Open-Meteo wind direction in degrees (direction FROM which wind blows)
 *   - windMph:    wind speed in mph
 *   - isDome:     true if stadium is dome / wind irrelevant
 */

import { computed } from 'vue'

const props = defineProps({
  cfBearing:  { type: [Number, String], default: null },
  windDirDeg: { type: [Number, String], default: null },
  windMph:    { type: [Number, String], default: null },
  isDome:     { type: Boolean, default: false },
})

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
  const cf = Number(props.cfBearing)
  if (!Number.isFinite(cf)) return null
  const blowingToward = (wd + 180) % 360
  return ((blowingToward - cf) + 360) % 360
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
    <!-- Gauge: 64x64 viewBox SVG, rendered ~48-56px.
         Diamond corners labeled CF (top), LF (left), RF (right), H (bottom).
         Small tick marks extend outward from each corner so the wind
         direction is unambiguous at a glance, no text-reading required.
         Arrow rotates from the diamond center. -->
    <svg
      class="wind-gauge"
      viewBox="0 0 64 64"
      xmlns="http://www.w3.org/2000/svg"
      :class="{ 'is-dome': isDome }"
    >
      <!-- Outward ticks at each vertex (drawn behind the diamond so
           the diamond stroke covers any visual overlap). -->
      <g v-if="!isDome" :stroke="'rgba(255,255,255,0.18)'" stroke-width="1" stroke-linecap="round">
        <!-- CF tick (up from top vertex) -->
        <line x1="32" y1="13" x2="32" y2="9" />
        <!-- H tick (down from bottom vertex) -->
        <line x1="32" y1="51" x2="32" y2="55" />
        <!-- LF tick (left from left vertex) -->
        <line x1="13" y1="32" x2="9" y2="32" />
        <!-- RF tick (right from right vertex) -->
        <line x1="51" y1="32" x2="55" y2="32" />
      </g>

      <!-- Diamond (rotated square) — home at bottom, CF at top -->
      <polygon
        points="32,14 50,32 32,50 14,32"
        fill="none"
        :stroke="isDome ? 'rgba(255,255,255,0.10)' : 'rgba(255,255,255,0.22)'"
        stroke-width="1"
      />

      <!-- Home plate marker (small dot at bottom vertex) -->
      <circle
        cx="32"
        cy="49"
        r="1.5"
        :fill="isDome ? 'rgba(255,255,255,0.20)' : 'rgba(255,255,255,0.55)'"
      />

      <!-- Corner labels — small, muted, hidden when dome -->
      <g v-if="!isDome" class="gauge-labels" fill="rgba(255,255,255,0.50)">
        <text x="32" y="7"  text-anchor="middle">CF</text>
        <text x="6"  y="34" text-anchor="start">LF</text>
        <text x="58" y="34" text-anchor="end">RF</text>
        <text x="32" y="62" text-anchor="middle">H</text>
      </g>

      <!-- Wind arrow.
           Drawn pointing UP (toward CF) by default at the center, then
           rotated to match the field-relative angle. 0deg = straight out to CF. -->
      <g
        v-if="showArrow"
        :transform="`rotate(${arrowRotation} 32 32)`"
        :style="{ filter: arrowGlow }"
      >
        <!-- Arrow shaft -->
        <line
          x1="32"
          y1="42"
          x2="32"
          y2="22"
          :stroke="arrowColor"
          stroke-width="2"
          stroke-linecap="round"
        />
        <!-- Arrow head -->
        <polyline
          points="27,27 32,21 37,27"
          fill="none"
          :stroke="arrowColor"
          stroke-width="2"
          stroke-linejoin="round"
          stroke-linecap="round"
        />
      </g>

      <!-- Dome marker: small "x" through the diamond -->
      <g v-else-if="isDome" stroke="rgba(255,255,255,0.25)" stroke-width="1" stroke-linecap="round">
        <line x1="20" y1="20" x2="44" y2="44" />
        <line x1="44" y1="20" x2="20" y2="44" />
      </g>
    </svg>

    <!-- mph readout next to gauge -->
    <div class="wind-mph">
      <span class="display-num wind-mph-num" :class="`tone-${windTone}`">{{ mphDisplay }}</span>
      <span v-if="mphSuffix" class="wind-mph-unit">{{ mphSuffix }}</span>
      <!-- TEMP DEBUG — remove after diagnosis -->
      <span
        v-if="!isDome"
        class="wind-debug"
        :title="`cf=${cfBearing} wd=${windDirDeg} angle=${fieldAngleDeg != null ? Math.round(fieldAngleDeg) : 'null'}`"
      >
        cf{{ cfBearing }} wd{{ windDirDeg }} a{{ fieldAngleDeg != null ? Math.round(fieldAngleDeg) : '∅' }}
      </span>
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
  width: 48px;
  height: 48px;
  display: block;
  flex-shrink: 0;
}
@media (min-width: 640px) {
  .wind-gauge {
    width: 56px;
    height: 56px;
  }
}
.wind-gauge.is-dome {
  opacity: 0.55;
}

/* Corner labels — small monospace, subtle so they don't fight the arrow */
.gauge-labels text {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 6.5px;
  font-weight: 600;
  letter-spacing: 0.02em;
  dominant-baseline: middle;
  user-select: none;
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

/* TEMP DEBUG style — remove after diagnosis */
.wind-debug {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 8px;
  color: rgba(255, 200, 80, 0.85);
  background: rgba(255, 200, 80, 0.08);
  padding: 1px 3px;
  margin-top: 2px;
  border: 1px solid rgba(255, 200, 80, 0.25);
  letter-spacing: 0;
  line-height: 1.2;
  white-space: nowrap;
}
</style>
