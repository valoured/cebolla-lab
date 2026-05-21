<script setup>
/**
 * TrendRowCompact.vue — dense one-line row for positions 7+ on /trends.
 *
 * Rendered below the hero grid of TrendCards. Trades visual drama for
 * scan density — ~52px tall, single inline bar showing L14 only, all
 * the same identity + matchup info but laid out horizontally.
 *
 * Math, tier system, and formatters all match TrendCard / TrendRow so
 * the trend chip is visually consistent across both sections.
 */

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { playerHeadshotUrl, teamLogoUrl, hideOnError } from '../utils/mlbImages.js'

const props = defineProps({
  row: { type: Object, required: true },
  metric: { type: String, required: true },
  rank: { type: Number, default: null },
})

const router = useRouter()

// ── Formatters (match TrendCard exactly) ──
// See TrendCard.vue for explanation of anchor_metric.
function unitFor() {
  return props.row?.anchor_metric || props.metric
}
function formatMetric(v) {
  if (v == null) return '—'
  if (unitFor() === 'iso') {
    return v.toFixed(3).replace(/^0/, '')
  }
  const pct = v * 100
  const decimals = pct < 10 ? 2 : 1
  return pct.toFixed(decimals) + '%'
}

// ── Trend tier ──
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

const trendPct = computed(() => {
  const ts = props.row.trend_score
  if (ts == null) return '—'
  const pct = Math.round(ts * 100)
  const sign = pct > 0 ? '+' : ''
  return `${sign}${pct}%`
})

// Compact bar: just L14 relative to season as scale anchor.
// Season is the gray ghost track behind, L14 fills the colored portion.
const barL14Width = computed(() => {
  const l14 = props.row.metric_l14
  const season = props.row.metric_season
  if (!l14 || !season) return 0
  const scaleMax = Math.max(season * 3, l14 * 1.1, 0.001)
  return Math.min(100, (l14 / scaleMax) * 100)
})

// ── Identity ──
const batsLabel = computed(() => {
  const b = props.row.batter?.bats
  if (b === 'L' || b === 'R') return `${b}HB`
  if (b === 'S') return 'SWITCH'
  return null
})

const matchupHint = computed(() => {
  const m = props.row.today_matchup
  if (!m) return null
  const hand = m.pitcher_throws ? ` (${m.pitcher_throws}HP)` : ''
  return `vs ${m.opponent_abbrev}${hand}`
})

const hasPlatoonEdge = computed(() => {
  const bats = props.row.batter?.bats
  const throws = props.row.today_matchup?.pitcher_throws
  if (!bats || !throws) return false
  if (bats === 'S') return true
  return bats !== throws
})

// ── Headshot ──
const headshot = computed(() => playerHeadshotUrl(props.row.batter?.mlbam_id, { size: 80 }))
const teamLogo = computed(() => teamLogoUrl(props.row.batter?.team_mlb_id))
const imgFailed = ref(false)
function onHeadshotError(e) {
  imgFailed.value = true
  if (e?.target) e.target.style.display = 'none'
}
const initials = computed(() => {
  const name = props.row.batter?.name || ''
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0][0].toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
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
    class="trc cursor-pointer"
    :class="[`trc--${trendTier}`]"
  >
    <!-- Rank -->
    <div v-if="rank" class="trc__rank label-bracket">
      {{ String(rank).padStart(2, '0') }}
    </div>

    <!-- Headshot -->
    <div class="trc__headshot-wrap">
      <img
        v-if="headshot && !imgFailed"
        :src="headshot"
        :alt="row.batter.name"
        class="trc__headshot"
        loading="lazy"
        @error="onHeadshotError"
      />
      <div
        v-if="!headshot || imgFailed"
        class="trc__headshot trc__headshot--initials"
        :aria-label="row.batter.name"
      >
        {{ initials }}
      </div>
      <img
        v-if="teamLogo"
        :src="teamLogo"
        :alt="row.batter.team_abbrev"
        class="trc__team-logo"
        loading="lazy"
        @error="hideOnError"
      />
    </div>

    <!-- Identity block -->
    <div class="trc__identity">
      <div class="trc__name-row">
        <span class="trc__name display-text">{{ row.batter.name }}</span>
        <span v-if="batsLabel" class="trc__bats label-bracket">{{ batsLabel }}</span>
      </div>
      <div class="trc__sub">
        <span class="trc__team">{{ row.batter.team_abbrev || '—' }}</span>
        <template v-if="matchupHint">
          <span class="trc__dot">·</span>
          <span class="trc__matchup">{{ matchupHint }}</span>
          <span v-if="hasPlatoonEdge" class="trc__platoon" title="Platoon advantage">▲</span>
        </template>
      </div>
    </div>

    <!-- L14 bar with inline value -->
    <div class="trc__bar-wrap">
      <span class="trc__bar-label">L14</span>
      <div class="trc__bar-track">
        <div
          class="trc__bar-fill"
          :style="{ width: `${barL14Width}%` }"
        ></div>
      </div>
      <span class="trc__bar-val display-num">{{ formatMetric(row.metric_l14) }}</span>
    </div>

    <!-- Trend chip -->
    <div class="trc__chip display-num">{{ trendPct }}</div>
  </article>
</template>

<style scoped>
.trc {
  display: grid;
  grid-template-columns: 36px 44px minmax(0, 1.4fr) minmax(0, 1.2fr) 80px;
  gap: 14px;
  align-items: center;
  padding: 8px 14px;
  background: rgba(14, 14, 18, 0.55);
  border: 1px solid;
  border-radius: 2px;
  position: relative;
  transition: background-color 150ms ease, border-color 150ms ease;
}
.trc:hover {
  background: rgba(22, 22, 27, 0.9);
}

/* On narrow screens the bar collapses below identity */
@media (max-width: 768px) {
  .trc {
    grid-template-columns: 36px 44px minmax(0, 1fr) 70px;
    gap: 10px;
  }
  .trc__bar-wrap {
    grid-column: 2 / -1;
    margin-top: 4px;
  }
}

.trc__rank {
  font-size: 9px;
  letter-spacing: 0.14em;
  color: rgba(155, 155, 168, 0.45);
  text-align: center;
}

/* Headshot (smaller than the hero card) */
.trc__headshot-wrap {
  position: relative;
  width: 40px;
  height: 40px;
  flex-shrink: 0;
}
.trc__headshot {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #16161B;
  border: 1px solid #26262E;
  object-fit: cover;
  object-position: center 22%;
  filter: grayscale(0.10) brightness(1.05);
  transition: filter 150ms ease, border-color 150ms ease;
}
.trc:hover .trc__headshot {
  filter: grayscale(0) brightness(1.10);
  border-color: rgba(255, 42, 42, 0.40);
}
.trc__headshot--initials {
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  font-weight: 500;
  color: #C6C6D0;
  background: linear-gradient(135deg, #1c1c22 0%, #16161B 100%);
  filter: none;
}
.trc__team-logo {
  position: absolute;
  bottom: -2px;
  right: -2px;
  width: 18px;
  height: 18px;
  object-fit: contain;
  filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.85))
          drop-shadow(0 1px 2px rgba(0, 0, 0, 0.6));
}

/* Identity */
.trc__identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.trc__name-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.trc__name {
  font-size: 14px;
  color: #E8E8EE;
  letter-spacing: -0.01em;
  line-height: 1.1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.trc__bats {
  font-size: 8px;
  opacity: 0.55;
  flex-shrink: 0;
}
.trc__sub {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: #9B9BA8;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.04em;
}
.trc__team {
  color: #C6C6D0;
}
.trc__dot {
  color: rgba(110, 110, 122, 0.5);
}
.trc__matchup {
  color: #9B9BA8;
}
.trc__platoon {
  color: #FFD23F;
  font-size: 9px;
  filter: drop-shadow(0 0 3px rgba(255, 210, 63, 0.55));
}
.trc__rest {
  color: rgba(110, 110, 122, 0.7);
  font-style: italic;
}

/* Bar */
.trc__bar-wrap {
  display: grid;
  grid-template-columns: 24px 1fr 48px;
  align-items: center;
  gap: 8px;
}
.trc__bar-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 8px;
  letter-spacing: 0.14em;
  color: #6E6E7A;
  text-transform: uppercase;
}
.trc__bar-track {
  height: 4px;
  background: rgba(38, 38, 46, 0.5);
  border-radius: 1px;
  overflow: hidden;
}
.trc__bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #FF2A2A 0%, #FF6B47 100%);
  box-shadow: 0 0 6px rgba(255, 42, 42, 0.35);
  transition: width 250ms ease;
  border-radius: 1px;
}
.trc__bar-val {
  font-size: 11px;
  color: #C6C6D0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

/* Trend chip — smaller than hero card */
.trc__chip {
  text-align: center;
  font-size: 14px;
  font-weight: 600;
  padding: 6px 10px;
  border: 1px solid;
  border-radius: 2px;
  font-variant-numeric: tabular-nums;
  color: var(--chip-color, #9B9BA8);
  background: var(--chip-bg, rgba(38, 38, 46, 0.5));
  border-color: var(--chip-border, #33333D);
}

/* ── Tier color tokens (consistent with TrendCard / TrendRow) ── */
.trc--blazing {
  border-color: rgba(255, 42, 42, 0.40);
  --chip-color: #FFE5E5;
  --chip-bg: rgba(255, 42, 42, 0.16);
  --chip-border: rgba(255, 42, 42, 0.55);
}
.trc--hot {
  border-color: rgba(255, 42, 42, 0.28);
  --chip-color: #FFB8B8;
  --chip-bg: rgba(255, 42, 42, 0.10);
  --chip-border: rgba(255, 42, 42, 0.38);
}
.trc--warm {
  border-color: rgba(255, 168, 74, 0.25);
  --chip-color: #FFD3A8;
  --chip-bg: rgba(255, 168, 74, 0.08);
  --chip-border: rgba(255, 168, 74, 0.32);
}
.trc--flat {
  border-color: rgba(38, 38, 46, 0.7);
  --chip-color: #9B9BA8;
  --chip-bg: rgba(38, 38, 46, 0.5);
  --chip-border: #33333D;
}
.trc--cool {
  border-color: rgba(79, 177, 221, 0.22);
  --chip-color: #A8D9EE;
  --chip-bg: rgba(79, 177, 221, 0.06);
  --chip-border: rgba(79, 177, 221, 0.28);
}
.trc--cold {
  border-color: rgba(79, 177, 221, 0.30);
  --chip-color: #4FB1DD;
  --chip-bg: rgba(58, 141, 188, 0.10);
  --chip-border: rgba(58, 141, 188, 0.38);
}
.trc--frozen {
  border-color: rgba(58, 141, 188, 0.42);
  --chip-color: #7CC8E5;
  --chip-bg: rgba(58, 141, 188, 0.16);
  --chip-border: rgba(58, 141, 188, 0.55);
}
</style>
