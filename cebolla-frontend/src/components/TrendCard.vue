<script setup>
/**
 * TrendCard.vue — hero card for top-N batters on the Trends/Streaks page.
 *
 * This is the "drama" treatment for the leaders — bigger headshot,
 * full L14-vs-SZN divergence bars, dominant trend chip. Below position
 * N (configurable, default 6) we hand off to the compact `TrendRow`.
 *
 * Card lifts the same math + tier system as TrendRow, just bigger
 * canvas and a different visual hierarchy. Keeping these as two
 * components rather than one with a `mode` prop because the templates
 * actually diverge enough that mode-switching would be messier than
 * two clean files.
 */

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { playerHeadshotUrl, teamLogoUrl, hideOnError } from '../utils/mlbImages.js'
import InfoTooltip from './InfoTooltip.vue'

const props = defineProps({
  row: { type: Object, required: true },
  metric: { type: String, required: true },
  metricLabel: { type: Object, required: true },
  rank: { type: Number, default: null },
})

const router = useRouter()

// ── Formatting helpers (matches TrendRowCompact exactly so values are consistent) ──
// `metric` is the active dropdown selection ('hr', 'combined', etc). When it's
// 'combined', the L14/SZN bar values represent whichever base metric was
// chosen as the anchor (usually HR, sometimes ISO/hits/barrel as fallback).
// row.anchor_metric tells us which formatter to apply. For base metrics it
// just matches `props.metric` and behavior is unchanged.
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

function formatDelta(d) {
  if (d == null) return ''
  if (unitFor() === 'iso') {
    const sign = d >= 0 ? '+' : ''
    return `${sign}${d.toFixed(3)}`
  }
  const sign = d >= 0 ? '+' : ''
  const pp = d * 100
  const decimals = Math.abs(pp) < 10 ? 2 : 1
  return `${sign}${pp.toFixed(decimals)}pp`
}

// ── Trend tier (matches TrendRow.vue exactly) ──
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

const chipSizeClass = computed(() => {
  const ts = props.row.trend_score
  if (ts == null) return ''
  const absPct = Math.abs(Math.round(ts * 100))
  if (absPct >= 1000) return 'trend-card__chip--xxs'
  if (absPct >= 100)  return 'trend-card__chip--xs'
  return ''
})

// ── Per-metric pips (Combined mode only) ──
// Tiny tier-colored badges showing each base metric's trend tier so the
// user can see WHY combined is hot/cold. Reuses the same tier system as
// the main chip — colors match across all displays.
function pipTier(ts) {
  if (ts == null) return 'flat'
  if (ts >= 0.50) return 'blazing'
  if (ts >= 0.25) return 'hot'
  if (ts >= 0.10) return 'warm'
  if (ts <= -0.50) return 'frozen'
  if (ts <= -0.25) return 'cold'
  if (ts <= -0.10) return 'cool'
  return 'flat'
}

const showPips = computed(() => props.metric === 'combined')

const pipsData = computed(() => {
  const tbm = props.row.trend_by_metric
  if (!tbm) return []
  return [
    { key: 'hr',     short: 'HR',  ts: tbm.hr },
    { key: 'hits',   short: 'H',   ts: tbm.hits },
    { key: 'barrel', short: 'BL',  ts: tbm.barrel },
    { key: 'iso',    short: 'ISO', ts: tbm.iso },
  ]
})

// ── Divergence bar widths (same algo as TrendRow) ──
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

// ── Identity helpers ──
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

// ── Headshot + fallback ──
const headshot = computed(() => playerHeadshotUrl(props.row.batter?.mlbam_id, { size: 160 }))
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
    class="trend-card group cursor-pointer"
    :class="[`trend-card--${trendTier}`]"
  >
    <!-- Rank badge: small, top-left -->
    <div v-if="rank" class="trend-card__rank label-bracket">
      {{ String(rank).padStart(2, '0') }}
    </div>

    <!-- Top row: headshot + trend chip dominates -->
    <div class="trend-card__top">
      <div class="trend-card__headshot-wrap">
        <img
          v-if="headshot && !imgFailed"
          :src="headshot"
          :alt="row.batter.name"
          class="trend-card__headshot"
          loading="lazy"
          @error="onHeadshotError"
        />
        <div
          v-if="!headshot || imgFailed"
          class="trend-card__headshot trend-card__headshot--initials"
          :aria-label="row.batter.name"
        >
          {{ initials }}
        </div>
        <img
          v-if="teamLogo"
          :src="teamLogo"
          :alt="row.batter.team_abbrev"
          class="trend-card__team-logo"
          loading="lazy"
          @error="hideOnError"
        />
      </div>

      <div class="trend-card__chip" :class="chipSizeClass">
        <div class="trend-card__chip-pct display-num">{{ trendPct }}</div>
        <div class="trend-card__chip-label">{{ trendLabel }}</div>
      </div>
    </div>

    <!-- Identity strip -->
    <div class="trend-card__identity">
      <div class="trend-card__name-row">
        <span class="trend-card__name display-text">{{ row.batter.name }}</span>
        <span v-if="batsLabel" class="trend-card__bats label-bracket">
          {{ batsLabel }}
        </span>
      </div>
      <div class="trend-card__sub">
        <span class="trend-card__team">{{ row.batter.team_abbrev || '—' }}</span>
        <template v-if="matchupHint">
          <span class="trend-card__dot">·</span>
          <span class="trend-card__matchup">{{ matchupHint }}</span>
          <span v-if="hasPlatoonEdge" class="trend-card__platoon" title="Platoon advantage">▲</span>
        </template>
      </div>
    </div>

    <!-- Divergence bars -->
    <div class="trend-card__divergence">
      <div class="trend-card__bar-row">
        <span class="trend-card__bar-label">L14</span>
        <div class="trend-card__bar-track">
          <div
            class="trend-card__bar-fill trend-card__bar-fill--l14"
            :style="{ width: `${barL14Width}%` }"
          ></div>
        </div>
        <span class="trend-card__bar-val display-num">
          {{ formatMetric(row.metric_l14) }}
        </span>
      </div>
      <div class="trend-card__bar-row">
        <span class="trend-card__bar-label">SZN</span>
        <div class="trend-card__bar-track">
          <div
            class="trend-card__bar-fill trend-card__bar-fill--season"
            :style="{ width: `${barSeasonWidth}%` }"
          ></div>
        </div>
        <span class="trend-card__bar-val display-num">
          {{ formatMetric(row.metric_season) }}
        </span>
      </div>
      <div class="trend-card__bar-meta">
        <span class="label-caps">Δ {{ formatDelta(row.metric_delta) }}</span>
        <span class="label-caps trend-card__sample">
          {{ row.pa_l14 }}/{{ row.pa_season }} PA
        </span>
      </div>
    </div>

    <!-- Sub-metric pips: shows tier per base metric so users can see what's
         driving the combined score. Only renders in Combined mode. -->
    <div v-if="showPips && pipsData.length" class="trend-card__pips">
      <span
        v-for="p in pipsData"
        :key="p.key"
        class="trend-card__pip"
        :class="`trend-card__pip--${pipTier(p.ts)}`"
        :title="`${p.short}: ${p.ts != null ? (p.ts > 0 ? '+' : '') + Math.round(p.ts * 100) + '%' : 'no data'}`"
      >
        {{ p.short }}
      </span>
    </div>
  </article>
</template>

<style scoped>
.trend-card {
  position: relative;
  padding: 12px 12px 10px;
  background: rgba(14, 14, 18, 0.7);
  border: 1px solid;
  border-radius: 2px;
  transition: background-color 200ms ease, border-color 200ms ease, transform 120ms ease;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.trend-card:hover {
  background: rgba(22, 22, 27, 0.95);
  transform: translateY(-2px);
}

.trend-card__rank {
  position: absolute;
  top: 5px;
  right: 8px;
  font-size: 8px;
  letter-spacing: 0.16em;
  color: rgba(155, 155, 168, 0.40);
}

/* Top: headshot left, trend chip right.
   Chip aligns to bottom of the row so it doesn't hug the card's top edge
   — the rank badge lives up there and the chip looked top-heavy when
   center-aligned with the headshot. */
.trend-card__top {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 8px;
  padding-top: 16px;
}

.trend-card__headshot-wrap {
  position: relative;
  flex-shrink: 0;
  width: 48px;
  height: 48px;
}
.trend-card__headshot {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: #16161B;
  border: 1px solid #26262E;
  object-fit: cover;
  object-position: center 22%;
  filter: grayscale(0.10) brightness(1.05);
  transition: filter 200ms ease, border-color 200ms ease;
}
.trend-card:hover .trend-card__headshot {
  filter: grayscale(0) brightness(1.10);
  border-color: rgba(255, 42, 42, 0.40);
}
.trend-card__headshot--initials {
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'JetBrains Mono', monospace;
  font-size: 14px;
  font-weight: 500;
  color: #C6C6D0;
  letter-spacing: 0.05em;
  background: linear-gradient(135deg, #1c1c22 0%, #16161B 100%);
  filter: none;
}
.trend-card__team-logo {
  position: absolute;
  bottom: -2px;
  right: -2px;
  width: 20px;
  height: 20px;
  object-fit: contain;
  filter: drop-shadow(0 0 2px rgba(0, 0, 0, 0.85))
          drop-shadow(0 1px 2px rgba(0, 0, 0, 0.6));
}

/* Trend chip — compact */
.trend-card__chip {
  border: 1px solid;
  padding: 5px 8px;
  min-width: 76px;
  text-align: center;
  border-radius: 2px;
  transition: transform 200ms ease;
  color: var(--chip-color, #9B9BA8);
  background: var(--chip-bg, rgba(38, 38, 46, 0.5));
  border-color: var(--chip-border, #33333D);
}
.trend-card:hover .trend-card__chip {
  transform: scale(1.04);
}
.trend-card__chip-pct {
  font-size: 17px;
  font-weight: 600;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.trend-card__chip--xs .trend-card__chip-pct {
  font-size: 14px;
}
.trend-card__chip--xxs .trend-card__chip-pct {
  font-size: 12px;
}
.trend-card__chip-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 7px;
  letter-spacing: 0.16em;
  margin-top: 2px;
  opacity: 0.85;
}

/* Identity */
.trend-card__identity {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.trend-card__name-row {
  display: flex;
  align-items: baseline;
  gap: 5px;
  flex-wrap: wrap;
}
.trend-card__name {
  font-size: 13px;
  color: #E8E8EE;
  letter-spacing: -0.01em;
  line-height: 1.1;
}
.trend-card__bats {
  font-size: 7px;
  opacity: 0.55;
}
.trend-card__sub {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 9px;
  color: #9B9BA8;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.04em;
  flex-wrap: wrap;
}
.trend-card__team {
  color: #C6C6D0;
  font-weight: 500;
}
.trend-card__dot {
  color: rgba(110, 110, 122, 0.5);
}
.trend-card__matchup {
  color: #9B9BA8;
}
.trend-card__platoon {
  color: #FFD23F;
  font-size: 9px;
  filter: drop-shadow(0 0 3px rgba(255, 210, 63, 0.55));
}

/* Divergence — tighter */
.trend-card__divergence {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 2px;
}
.trend-card__bar-row {
  display: grid;
  grid-template-columns: 22px 1fr 44px;
  align-items: center;
  gap: 6px;
}
.trend-card__bar-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 7px;
  letter-spacing: 0.14em;
  color: #6E6E7A;
  text-transform: uppercase;
}
.trend-card__bar-track {
  height: 4px;
  background: rgba(38, 38, 46, 0.5);
  border-radius: 1px;
  overflow: hidden;
}
.trend-card__bar-fill {
  height: 100%;
  transition: width 300ms ease;
  border-radius: 1px;
}
.trend-card__bar-fill--l14 {
  background: linear-gradient(90deg, #FF2A2A 0%, #FF6B47 100%);
  box-shadow: 0 0 6px rgba(255, 42, 42, 0.35);
}
.trend-card__bar-fill--season {
  background: rgba(155, 155, 168, 0.55);
}
.trend-card__bar-val {
  font-size: 10px;
  color: #C6C6D0;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.trend-card__bar-meta {
  display: flex;
  justify-content: space-between;
  padding-left: 28px;
  margin-top: 2px;
  font-size: 7px;
  opacity: 0.7;
}
.trend-card__sample {
  color: rgba(110, 110, 122, 0.7);
}

/* ── Sub-metric pips (Combined mode) ──
   4 tiny tier-tinted chips showing per-metric tier so users see what's
   driving the Combined Heat score. */
.trend-card__pips {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  padding-top: 8px;
  border-top: 1px dashed rgba(38, 38, 46, 0.7);
  flex-wrap: wrap;
}
.trend-card__pip {
  font-family: 'JetBrains Mono', monospace;
  font-size: 8px;
  letter-spacing: 0.08em;
  padding: 2px 6px;
  border: 1px solid;
  border-radius: 1px;
  cursor: help;
  font-weight: 500;
  color: var(--pip-color, #9B9BA8);
  background: var(--pip-bg, rgba(38, 38, 46, 0.4));
  border-color: var(--pip-border, #33333D);
  text-transform: uppercase;
}

.trend-card__pip--blazing {
  --pip-color: #FFE5E5;
  --pip-bg: rgba(255, 42, 42, 0.20);
  --pip-border: rgba(255, 42, 42, 0.55);
}
.trend-card__pip--hot {
  --pip-color: #FFB8B8;
  --pip-bg: rgba(255, 42, 42, 0.12);
  --pip-border: rgba(255, 42, 42, 0.40);
}
.trend-card__pip--warm {
  --pip-color: #FFD3A8;
  --pip-bg: rgba(255, 168, 74, 0.10);
  --pip-border: rgba(255, 168, 74, 0.35);
}
.trend-card__pip--flat {
  --pip-color: #6E6E7A;
  --pip-bg: rgba(38, 38, 46, 0.4);
  --pip-border: #33333D;
}
.trend-card__pip--cool {
  --pip-color: #A8D9EE;
  --pip-bg: rgba(79, 177, 221, 0.07);
  --pip-border: rgba(79, 177, 221, 0.28);
}
.trend-card__pip--cold {
  --pip-color: #4FB1DD;
  --pip-bg: rgba(58, 141, 188, 0.10);
  --pip-border: rgba(58, 141, 188, 0.38);
}
.trend-card__pip--frozen {
  --pip-color: #7CC8E5;
  --pip-bg: rgba(58, 141, 188, 0.18);
  --pip-border: rgba(58, 141, 188, 0.55);
}

/* ── Tier colors (matches TrendRow's system) ── */
.trend-card--blazing {
  border-color: rgba(255, 42, 42, 0.55);
  --chip-color: #FFE5E5;
  --chip-bg: rgba(255, 42, 42, 0.18);
  --chip-border: rgba(255, 42, 42, 0.65);
}
.trend-card--hot {
  border-color: rgba(255, 42, 42, 0.38);
  --chip-color: #FFB8B8;
  --chip-bg: rgba(255, 42, 42, 0.12);
  --chip-border: rgba(255, 42, 42, 0.45);
}
.trend-card--warm {
  border-color: rgba(255, 168, 74, 0.32);
  --chip-color: #FFD3A8;
  --chip-bg: rgba(255, 168, 74, 0.10);
  --chip-border: rgba(255, 168, 74, 0.40);
}
.trend-card--flat {
  border-color: #26262E;
  --chip-color: #9B9BA8;
  --chip-bg: rgba(38, 38, 46, 0.5);
  --chip-border: #33333D;
}
.trend-card--cool {
  border-color: rgba(79, 177, 221, 0.28);
  --chip-color: #A8D9EE;
  --chip-bg: rgba(79, 177, 221, 0.08);
  --chip-border: rgba(79, 177, 221, 0.35);
}
.trend-card--cold {
  border-color: rgba(79, 177, 221, 0.40);
  --chip-color: #4FB1DD;
  --chip-bg: rgba(58, 141, 188, 0.12);
  --chip-border: rgba(58, 141, 188, 0.45);
}
.trend-card--frozen {
  border-color: rgba(58, 141, 188, 0.55);
  --chip-color: #7CC8E5;
  --chip-bg: rgba(58, 141, 188, 0.18);
  --chip-border: rgba(58, 141, 188, 0.65);
}
</style>
