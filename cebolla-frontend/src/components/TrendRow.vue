<script setup>
/**
 * TrendRow.vue — one batter on the Trends/Streaks page.
 *
 * Showcases:
 *   - Headshot + name + team abbrev chip + bats hand
 *   - Today's matchup (opponent + opposing pitcher + handedness)
 *   - Big metric: L14 value (e.g. "12.4%") with delta vs season
 *   - Mini sparkline / divergence bar (L14 vs Season, visualized)
 *   - Trend score chip with hot/cold color
 *
 * Heavy intentionally — this is the visual centerpiece of /trends.
 */

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { playerHeadshotUrl, teamLogoUrl, hideOnError } from '../utils/mlbImages.js'
import InfoTooltip from './InfoTooltip.vue'

const props = defineProps({
  row: { type: Object, required: true },
  metric: { type: String, required: true },
  metricLabel: { type: Object, required: true },  // { short, long, unit }
  rank: { type: Number, default: null },
})

const router = useRouter()

// Format a metric value for display.
//   - ISO: slash-line style (.234)
//   - Other rate metrics: percent. When the value is under 10% we use
//     2 decimals (3.13%) so users can reproduce the trend math from
//     what's on screen. When 10%+ we use 1 decimal (24.7%) since the
//     extra precision adds visual weight without changing the read.
function formatMetric(v) {
  if (v == null) return '—'
  if (props.metric === 'iso') {
    return v.toFixed(3).replace(/^0/, '')   // .234 style
  }
  const pct = v * 100
  const decimals = pct < 10 ? 2 : 1
  return pct.toFixed(decimals) + '%'
}

// Delta is the absolute change (l14 - season) on whatever scale the
// metric uses. Use the same adaptive decimals as formatMetric so the
// delta number is reproducible from the L14 and SZN values shown above.
function formatDelta(d) {
  if (d == null) return ''
  if (props.metric === 'iso') {
    const sign = d >= 0 ? '+' : ''
    return `${sign}${d.toFixed(3)}`
  }
  const sign = d >= 0 ? '+' : ''
  const pp = d * 100
  // Decimal count keyed to magnitude — small deltas get extra precision
  // so they don't display as "+0.0pp" when the real delta is +0.4pp.
  const decimals = Math.abs(pp) < 10 ? 2 : 1
  return `${sign}${pp.toFixed(decimals)}pp`
}

// Trend score → tier name. The actual colors live in scoped CSS so we
// don't depend on Tailwind's JIT scanner picking up dynamic strings
// (which it can't reliably do from script-section computed values).
//   |ts| < 0.10  → flat   (neutral)
//   0.10 - 0.25 → warm/cool
//   0.25 - 0.50 → hot/cold
//   > 0.50      → blazing/frozen
const trendTier = computed(() => {
  const ts = props.row.trend_score
  if (ts == null) return 'flat'
  if (ts >= 0.50) return 'blazing'
  if (ts >= 0.25) return 'hot'
  if (ts >= 0.10) return 'warm'
  if (ts <= -0.50) return 'frozen'
  if (ts <= -0.25) return 'cold'
  if (ts <= -0.10) return 'cool'
  return 'flat'
})

const trendLabel = computed(() => {
  const ts = props.row.trend_score
  if (ts == null) return '—'
  if (ts >= 0.50) return 'BLAZING'
  if (ts >= 0.25) return 'HOT'
  if (ts >= 0.10) return 'WARM'
  if (ts <= -0.50) return 'FROZEN'
  if (ts <= -0.25) return 'COLD'
  if (ts <= -0.10) return 'COOL'
  return 'FLAT'
})

const trendPct = computed(() => {
  const ts = props.row.trend_score
  if (ts == null) return '—'
  const pct = Math.round(ts * 100)
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct}%`
})

// Divergence bar: visualizes L14 relative to Season on a horizontal track.
// We use the season value as the anchor (50% mark) and L14 as the bar.
// The wider/taller the bar deviates from center, the bigger the trend.
//
// Width is computed against a stable scale max (3x season) so the
// proportions stay readable across players.
const barL14Width = computed(() => {
  const l14 = props.row.metric_l14
  const season = props.row.metric_season
  if (!l14 || !season) return 0
  const scaleMax = Math.max(season * 3, l14 * 1.1, 0.001)
  return Math.min(100, (l14 / scaleMax) * 100)
})
const barSeasonWidth = computed(() => {
  const season = props.row.metric_season
  const l14 = props.row.metric_l14
  if (!season) return 0
  const scaleMax = Math.max(season * 3, (l14 || 0) * 1.1, 0.001)
  return Math.min(100, (season / scaleMax) * 100)
})

// Today matchup hint — "vs WSH (LHP)"
const matchupHint = computed(() => {
  const m = props.row.today_matchup
  if (!m) return null
  const hand = m.pitcher_throws ? ` (${m.pitcher_throws}HP)` : ''
  return `vs ${m.opponent_abbrev}${hand}`
})

// Platoon advantage badge — LHB vs RHP or RHB vs LHP. The classic
// platoon edge in baseball; if Cebolla rows surface it we save the user
// a lookup.
const hasPlatoonEdge = computed(() => {
  const bats = props.row.batter?.bats
  const throws = props.row.today_matchup?.pitcher_throws
  if (!bats || !throws) return false
  if (bats === 'S') return true   // switch hitters always platoon
  return bats !== throws
})

// Request 128px from MLB so a 56px display (2x retina ≈ 112px) renders
// crisp on high-DPI screens without aliasing.
const headshot = computed(() => playerHeadshotUrl(props.row.batter?.mlbam_id, { size: 128 }))
const teamLogo = computed(() => teamLogoUrl(props.row.batter?.team_mlb_id))

// Bats handedness display:
//   - 'L' / 'R'   → "LHB" / "RHB"  (chip shown)
//   - 'S'         → "SWITCH"        (chip shown)
//   - null/?/etc  → chip suppressed entirely (no point showing "?HB")
const batsLabel = computed(() => {
  const b = props.row.batter?.bats
  if (b === 'L' || b === 'R') return `${b}HB`
  if (b === 'S') return 'SWITCH'
  return null
})

// Headshot fallback — when MLB doesn't have a photo for the player
// (often the case with September call-ups), the URL still resolves but
// the image renders as a generic silhouette. We replace with initials
// in a circle for a cleaner visual.
//
// `imgFailed` flips true the first time the <img>'s onerror fires.
const imgFailed = ref(false)
function onHeadshotError(e) {
  imgFailed.value = true
  // Don't call hideOnError — we replace, not hide.
  if (e?.target) e.target.style.display = 'none'
}
const initials = computed(() => {
  const name = props.row.batter?.name || ''
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0][0].toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
})

// Trend score formatter outputs strings like "+322%" or "+100%" — once
// the magnitude is over 99 we need an extra digit's worth of space.
// Auto-shrink the font instead of letting it overflow the chip.
const chipSizeClass = computed(() => {
  const ts = props.row.trend_score
  if (ts == null) return ''
  const absPct = Math.abs(Math.round(ts * 100))
  if (absPct >= 1000) return 'trend-row__score-chip--xxs'   // 4-digit
  if (absPct >= 100)  return 'trend-row__score-chip--xs'    // 3-digit
  return ''
})

function openPlayer() {
  if (props.row.batter?.id) {
    router.push({ name: 'player', params: { playerId: props.row.batter.id } })
  }
}
</script>

<template>
  <article
    @click="openPlayer"
    class="trend-row group cursor-pointer"
    :class="[`trend-row--${trendTier}`]"
  >
    <!-- Rank chip (top-left, subtle) -->
    <div v-if="rank" class="trend-row__rank label-bracket">
      {{ String(rank).padStart(2, '0') }}
    </div>

    <!-- Left cluster: headshot + identity -->
    <div class="trend-row__identity">
      <div class="trend-row__headshot-wrap">
        <img
          v-if="headshot && !imgFailed"
          :src="headshot"
          :alt="row.batter.name"
          class="trend-row__headshot"
          loading="lazy"
          @error="onHeadshotError"
        />
        <!-- Initials fallback when MLB has no photo or load fails -->
        <div
          v-if="!headshot || imgFailed"
          class="trend-row__headshot trend-row__headshot--initials"
          :aria-label="row.batter.name"
        >
          {{ initials }}
        </div>
        <img
          v-if="teamLogo"
          :src="teamLogo"
          :alt="row.batter.team_abbrev"
          class="trend-row__team-logo"
          loading="lazy"
          @error="hideOnError"
        />
      </div>

      <div class="trend-row__name-block">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="trend-row__name display-text">{{ row.batter.name }}</span>
          <span v-if="batsLabel" class="trend-row__bats label-bracket">
            {{ batsLabel }}
          </span>
        </div>
        <div class="trend-row__sub">
          <span class="trend-row__team">{{ row.batter.team_abbrev || '—' }}</span>
          <template v-if="matchupHint">
            <span class="trend-row__dot">·</span>
            <span class="trend-row__matchup">{{ matchupHint }}</span>
            <template v-if="hasPlatoonEdge">
              <span class="trend-row__platoon" aria-label="Platoon advantage">▲</span>
              <InfoTooltip term="platoon_advantage" size="sm" position="bottom" />
            </template>
          </template>
          <template v-else-if="!row.playing_today">
            <span class="trend-row__dot">·</span>
            <span class="trend-row__rest">not playing</span>
          </template>
        </div>
      </div>
    </div>

    <!-- Middle: divergence visualization -->
    <div class="trend-row__divergence">
      <div class="trend-row__bar-row">
        <span class="trend-row__bar-label" :title="'Last 14 days — see info icon at top'">L14</span>
        <div class="trend-row__bar-track">
          <div
            class="trend-row__bar-fill trend-row__bar-fill--l14"
            :style="{ width: `${barL14Width}%` }"
          ></div>
        </div>
        <span class="trend-row__bar-val display-num">
          {{ formatMetric(row.metric_l14) }}
        </span>
      </div>
      <div class="trend-row__bar-row">
        <span class="trend-row__bar-label" :title="'Season to date'">SZN</span>
        <div class="trend-row__bar-track">
          <div
            class="trend-row__bar-fill trend-row__bar-fill--season"
            :style="{ width: `${barSeasonWidth}%` }"
          ></div>
        </div>
        <span class="trend-row__bar-val display-num">
          {{ formatMetric(row.metric_season) }}
        </span>
      </div>
      <div class="trend-row__bar-meta">
        <span class="label-caps trend-row__delta-label" :title="'Absolute change from season to L14 (percentage points for rate metrics)'">
          Δ {{ formatDelta(row.metric_delta) }}
        </span>
        <span class="label-caps trend-row__sample" :title="'Plate appearances: L14 / season'">
          {{ row.pa_l14 }}/{{ row.pa_season }} PA
        </span>
      </div>
    </div>

    <!-- Right: trend score chip -->
    <div class="trend-row__score">
      <div class="trend-row__score-chip" :class="chipSizeClass">
        <div class="trend-row__score-pct display-num">{{ trendPct }}</div>
        <div class="trend-row__score-label">{{ trendLabel }}</div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.trend-row {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1.4fr) auto;
  gap: 18px;
  align-items: center;
  padding: 22px 18px 16px;
  background: rgba(14, 14, 18, 0.7);
  border: 1px solid;
  border-radius: 2px;
  position: relative;
  transition: background-color 200ms ease, border-color 200ms ease, transform 120ms ease;
}
.trend-row:hover {
  background: rgba(22, 22, 27, 0.95);
  transform: translateX(2px);
}
@media (max-width: 768px) {
  .trend-row {
    grid-template-columns: 1fr;
    gap: 12px;
  }
}

.trend-row__rank {
  position: absolute;
  top: 8px;
  right: 14px;
  font-size: 9px;
  letter-spacing: 0.18em;
  color: rgba(155, 155, 168, 0.45);
}

/* Identity column */
.trend-row__identity {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}
.trend-row__headshot-wrap {
  position: relative;
  flex-shrink: 0;
  width: 56px;
  height: 56px;
}
.trend-row__headshot {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: #16161B;
  border: 1px solid #26262E;
  object-fit: cover;
  /* 22% from top of the source image — MLB headshots have the face
     centered around the top third of the source crop, so anchoring
     "center top" cuts chins. Shifting down ~22% keeps the head framed
     without losing the neck. */
  object-position: center 22%;
  filter: grayscale(0.10) brightness(1.05);
  transition: filter 200ms ease, border-color 200ms ease;
}
/* Initials-in-circle fallback when MLB has no photo for the player. */
.trend-row__headshot--initials {
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 16px;
  font-weight: 500;
  color: #C6C6D0;
  letter-spacing: 0.05em;
  background: linear-gradient(135deg, #1c1c22 0%, #16161B 100%);
  filter: none;
}
.trend-row:hover .trend-row__headshot--initials {
  border-color: rgba(255, 42, 42, 0.40);
  color: #E8E8EE;
}
.trend-row:hover .trend-row__headshot {
  filter: grayscale(0) brightness(1.10);
  border-color: rgba(255, 42, 42, 0.40);
}
.trend-row__team-logo {
  position: absolute;
  bottom: -2px;
  right: -2px;
  width: 24px;
  height: 24px;
  /* Bare logo overlay — no circular chrome. Drop-shadow keeps it
     legible against varied headshot backgrounds without adding a
     filled bg pill. */
  object-fit: contain;
  filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.85))
          drop-shadow(0 1px 2px rgba(0, 0, 0, 0.6));
}

.trend-row__name-block {
  min-width: 0;
}
.trend-row__name {
  font-size: 16px;
  color: #E8E8EE;
  letter-spacing: -0.01em;
  line-height: 1.1;
}
.trend-row__bats {
  font-size: 9px;
  opacity: 0.65;
}
.trend-row__sub {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
  font-size: 11px;
  color: #9B9BA8;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.04em;
  flex-wrap: wrap;
}
.trend-row__team {
  color: #C6C6D0;
  font-weight: 500;
}
.trend-row__dot {
  color: rgba(110, 110, 122, 0.5);
}
.trend-row__matchup {
  color: #9B9BA8;
}
.trend-row__rest {
  color: rgba(110, 110, 122, 0.7);
  font-style: italic;
}
.trend-row__platoon {
  color: #FFD23F;
  font-size: 10px;
  filter: drop-shadow(0 0 3px rgba(255, 210, 63, 0.55));
}

/* Divergence visualization */
.trend-row__divergence {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.trend-row__bar-row {
  display: grid;
  grid-template-columns: 32px 1fr 64px;
  align-items: center;
  gap: 10px;
}
.trend-row__bar-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.14em;
  color: #6E6E7A;
  text-transform: uppercase;
}
.trend-row__bar-track {
  height: 6px;
  background: rgba(38, 38, 46, 0.5);
  border-radius: 1px;
  overflow: hidden;
  position: relative;
}
.trend-row__bar-fill {
  height: 100%;
  transition: width 300ms ease;
  border-radius: 1px;
}
.trend-row__bar-fill--l14 {
  background: linear-gradient(90deg, #FF2A2A 0%, #FF6B47 100%);
  box-shadow: 0 0 8px rgba(255, 42, 42, 0.40);
}
.trend-row__bar-fill--season {
  background: rgba(155, 155, 168, 0.55);
}
.trend-row__bar-val {
  font-size: 12px;
  color: #C6C6D0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.trend-row__bar-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  padding-left: 42px;   /* align under bars */
  font-size: 9px;
  opacity: 0.7;
}
.trend-row__sample {
  color: rgba(110, 110, 122, 0.7);
}

/* Trend score chip */
.trend-row__score {
  flex-shrink: 0;
}
.trend-row__score-chip {
  border: 1px solid;
  padding: 10px 14px;
  min-width: 110px;
  text-align: center;
  border-radius: 2px;
  transition: transform 200ms ease;
}
.trend-row:hover .trend-row__score-chip {
  transform: scale(1.03);
}
.trend-row__score-pct {
  font-size: 22px;
  font-weight: 600;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
/* Auto-shrink the % when the value is 3 or 4 digits long, so big trends
   like "+322%" don't break out of the chip border. */
.trend-row__score-chip--xs .trend-row__score-pct {
  font-size: 18px;
}
.trend-row__score-chip--xxs .trend-row__score-pct {
  font-size: 15px;
}
.trend-row__score-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.18em;
  margin-top: 4px;
  opacity: 0.85;
}

/* ── Tier-specific color tokens ───────────────────────────────
   Each tier sets both the row border AND the inner chip colors via
   CSS custom props the chip picks up. This way Tailwind JIT doesn't
   have to figure out dynamic class names — colors are guaranteed
   to be in the bundle.
*/
.trend-row--blazing {
  border-color: rgba(255, 42, 42, 0.55);
  --chip-color: #FFE5E5;
  --chip-bg: rgba(255, 42, 42, 0.18);
  --chip-border: rgba(255, 42, 42, 0.65);
}
.trend-row--hot {
  border-color: rgba(255, 42, 42, 0.38);
  --chip-color: #FFB8B8;
  --chip-bg: rgba(255, 42, 42, 0.12);
  --chip-border: rgba(255, 42, 42, 0.45);
}
.trend-row--warm {
  border-color: rgba(255, 168, 74, 0.32);
  --chip-color: #FFD3A8;
  --chip-bg: rgba(255, 168, 74, 0.10);
  --chip-border: rgba(255, 168, 74, 0.40);
}
.trend-row--flat {
  border-color: #26262E;
  --chip-color: #9B9BA8;
  --chip-bg: rgba(38, 38, 46, 0.5);
  --chip-border: #33333D;
}
.trend-row--cool {
  border-color: rgba(79, 177, 221, 0.28);
  --chip-color: #A8D9EE;
  --chip-bg: rgba(79, 177, 221, 0.08);
  --chip-border: rgba(79, 177, 221, 0.35);
}
.trend-row--cold {
  border-color: rgba(79, 177, 221, 0.40);
  --chip-color: #4FB1DD;
  --chip-bg: rgba(58, 141, 188, 0.12);
  --chip-border: rgba(58, 141, 188, 0.45);
}
.trend-row--frozen {
  border-color: rgba(58, 141, 188, 0.55);
  --chip-color: #7CC8E5;
  --chip-bg: rgba(58, 141, 188, 0.18);
  --chip-border: rgba(58, 141, 188, 0.65);
}

.trend-row__score-chip {
  color: var(--chip-color, #9B9BA8);
  background: var(--chip-bg, rgba(38, 38, 46, 0.5));
  border-color: var(--chip-border, #33333D) !important;
}
</style>
