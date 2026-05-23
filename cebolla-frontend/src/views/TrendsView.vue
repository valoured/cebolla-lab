<script setup>
/**
 * TrendsView.vue — Cebolla Trends (M.07)
 *
 * Streaks-style page: ranks batters by L14-vs-season divergence on a
 * chosen metric (HR rate / Hit rate / Barrel rate / ISO). Default view
 * is "Hot" (positive divergence) for players playing today.
 *
 * Distinct from Krashboard's Streaks page:
 *   - We use L14 vs Season divergence ("trend score") rather than raw
 *     consecutive-game streaks. That's a more honest signal until we
 *     have the per-game batter log in place (Part 2, future cron).
 *   - Tonight's matchup is integrated inline with a platoon-advantage
 *     pip — Krashboard makes you click through.
 *   - Cebolla aesthetic: signal-red trend bar + JetBrains Mono numerics.
 */

import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useTrends } from '../composables/useTrends.js'
import LoadingBrand from '../components/LoadingBrand.vue'
import TrendCard from '../components/TrendCard.vue'
import TrendRowCompact from '../components/TrendRowCompact.vue'
import InfoTooltip from '../components/InfoTooltip.vue'

// Each metric maps to its glossary term so the per-pill tooltip can
// pull the right entry.
const METRIC_TERM = {
  combined: 'trend_combined',
  hr:       'trend_hr_pa',
  hits:     'trend_h_pa',
  barrel:   'trend_barrel',
  iso:      'trend_iso',
}

// How many leaders get the hero-card treatment vs the compact tail.
// 8 fits cleanly as a 4×2 grid on desktop and 2×4 on tablet.
const HERO_COUNT = 8

const {
  rows,
  stats,
  loading,
  error,
  metric,
  direction,
  playingTodayOnly,
  minPA,
  METRIC_LABELS,
  TREND_METRICS,
} = useTrends()

// Metric label for the header
const currentMetricLabel = computed(() => METRIC_LABELS[metric.value] || {})

// Header dates (consistent with SlateView)
//
// dateTick keeps these reactive across the midnight ET boundary. Bumped
// once per minute so the header date label updates if a user leaves the
// page open overnight, matching the underlying data refresh cadence.
const dateTick = ref(0)
let dateTimer = null
onMounted(() => { dateTimer = setInterval(() => dateTick.value++, 60_000) })
onUnmounted(() => { if (dateTimer) clearInterval(dateTimer) })
const today = computed(() => {
  dateTick.value  // reactive dep
  return new Date()
})
const dateShort = computed(() => today.value.toLocaleDateString('en-US', {
  weekday: 'short', month: 'short', day: 'numeric',
}))

// Max rows shown — performance guard. 100 is plenty (Krashboard
// typically shows the top 30-50). User can refine via filters.
const displayCap = 100

const cappedRows = computed(() => rows.value.slice(0, displayCap))

// Hybrid layout split: hero cards for the leaders, compact rows for the tail.
// If we have fewer than HERO_COUNT qualified rows, everyone becomes a hero
// card and there's no tail.
const heroRows = computed(() => cappedRows.value.slice(0, HERO_COUNT))
const tailRows = computed(() => cappedRows.value.slice(HERO_COUNT))

// Description text under the page title — explains how the trend score
// is computed in plain English so users don't have to read the
// methodology page.
const trendDescription = computed(() => {
  const dir = direction.value === 'cold' ? 'cooling off' : 'heating up'
  const filter = playingTodayOnly.value ? 'in today\'s slate' : 'league-wide'
  if (metric.value === 'combined') {
    return `Batters ${dir} across multiple signals — geometric mean of HR rate, hit rate, barrel%, and ISO divergence, ${filter}.`
  }
  const m = currentMetricLabel.value.long || 'Metric'
  return `Batters ${dir} on ${m.toLowerCase()} — L14 form vs season baseline, ${filter}.`
})
</script>

<template>
  <div class="min-h-screen">
    <!-- Header -->
    <header class="px-4 sm:px-6 pt-6 sm:pt-8 pb-4 sm:pb-5 border-b border-bg-200">
      <div class="flex flex-col md:flex-row md:items-end md:justify-between gap-4 md:gap-6 mb-4">
        <div>
          <div class="flex items-center gap-3 mb-2">
            <span class="label-bracket text-signal-400">trends · M.07</span>
            <span class="label-caps">{{ dateShort }}</span>
          </div>
          <h1 class="display-text text-2xl sm:text-3xl text-fg-800 tracking-tight leading-none mb-2">
            Heat Map
          </h1>
          <p class="text-fg-500 text-sm max-w-2xl mt-2 leading-relaxed">
            {{ trendDescription }}
          </p>
        </div>

        <!-- Stats strip -->
        <div class="flex items-center gap-3 sm:gap-5 text-left md:text-right">
          <div>
            <div class="label-caps">Qualified</div>
            <div class="display-num text-xl sm:text-2xl text-fg-700 leading-none mt-1">
              {{ stats.total_qualified }}
            </div>
          </div>
          <div class="w-px h-7 sm:h-8 bg-bg-200"></div>
          <div>
            <div class="label-caps">Today</div>
            <div class="display-num text-xl sm:text-2xl text-signal-200 leading-none mt-1">
              {{ stats.playing_today }}
            </div>
          </div>
          <div class="w-px h-7 sm:h-8 bg-bg-200 hidden sm:block"></div>
          <div class="hidden sm:block">
            <div class="label-caps trends-hot-cold-label">
              <span class="trends-hot-tint">Hot</span>
              <span class="opacity-50 mx-1">·</span>
              <span class="trends-cold-tint">Cold</span>
            </div>
            <div class="display-num text-xs text-fg-500 leading-none mt-1">
              <span class="text-signal-200">{{ stats.hot_count }}</span>
              <span class="opacity-50 mx-1">·</span>
              <span class="text-edge-cold-1">{{ stats.cold_count }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Filter rail -->
      <div class="trends-controls">
        <!-- Metric toggles -->
        <div class="trends-controls__group">
          <span class="trends-controls__group-label">
            <span class="label-caps">metric</span>
          </span>
          <div class="trends-controls__pills">
            <button
              v-for="m in TREND_METRICS"
              :key="m"
              @click="metric = m"
              class="trends-pill"
              :class="{ 'trends-pill--active': metric === m }"
              :title="METRIC_LABELS[m]?.long"
            >
              {{ METRIC_LABELS[m]?.short || m }}
            </button>
            <!-- One tooltip for the active metric; updates as user toggles -->
            <InfoTooltip
              v-if="METRIC_TERM[metric]"
              :term="METRIC_TERM[metric]"
              size="sm"
              position="bottom"
              class="trends-controls__pill-info"
            />
          </div>
        </div>

        <!-- Direction toggle -->
        <div class="trends-controls__group">
          <span class="label-caps trends-controls__group-label">direction</span>
          <div class="trends-controls__pills">
            <button
              @click="direction = 'hot'"
              class="trends-pill"
              :class="{ 'trends-pill--active trends-pill--hot': direction === 'hot' }"
            >
              <span class="trends-pill__icon">▲</span> Hot
            </button>
            <button
              @click="direction = 'cold'"
              class="trends-pill"
              :class="{ 'trends-pill--active trends-pill--cold': direction === 'cold' }"
            >
              <span class="trends-pill__icon">▼</span> Cold
            </button>
          </div>
        </div>

        <!-- Playing today toggle -->
        <div class="trends-controls__group">
          <span class="label-caps trends-controls__group-label">scope</span>
          <div class="trends-controls__pills">
            <button
              @click="playingTodayOnly = true"
              class="trends-pill"
              :class="{ 'trends-pill--active': playingTodayOnly }"
            >
              Today
            </button>
            <button
              @click="playingTodayOnly = false"
              class="trends-pill"
              :class="{ 'trends-pill--active': !playingTodayOnly }"
            >
              League
            </button>
          </div>
        </div>

        <!-- Min PA slider -->
        <div class="trends-controls__group">
          <span class="trends-controls__group-label flex items-center">
            <span class="label-caps">min PA</span>
            <InfoTooltip term="min_pa" size="sm" position="bottom" />
          </span>
          <div class="trends-controls__pa">
            <input
              type="range"
              min="10"
              max="60"
              step="5"
              v-model.number="minPA"
              class="trends-controls__slider"
            />
            <span class="display-num text-xs text-fg-600 w-6 text-right">{{ minPA }}</span>
          </div>
        </div>
      </div>
    </header>

    <!-- Content -->
    <section class="px-4 sm:px-6 py-5 sm:py-6">
      <LoadingBrand v-if="loading" text="Peeling layers…" />

      <div v-else-if="error" class="border border-signal-400/30 bg-signal-400/5 p-5">
        <div class="label-bracket text-signal-400 mb-2">connection error</div>
        <div class="text-fg-600 font-mono text-sm">{{ error }}</div>
      </div>

      <div v-else-if="!rows.length" class="text-center py-20">
        <img
          src="/cebolla-wordmark.png"
          alt="Cebolla"
          class="cebolla-wordmark-empty mx-auto mb-6"
        />
        <div class="display-text text-2xl text-fg-500 italic mb-3">No qualified batters</div>
        <div class="text-fg-500 text-sm max-w-md mx-auto leading-relaxed">
          Try lowering the min PA threshold or switching to League scope.<br>
          <span class="text-fg-400">L14 data fills in nightly as the cron runs.</span>
        </div>
      </div>

      <div v-else class="max-w-6xl mx-auto">
        <!-- ── Hero section: top N as a grid of TrendCards ── -->
        <div class="flex items-baseline justify-between px-1 mb-3">
          <div class="flex items-baseline gap-3">
            <span class="label-bracket text-signal-400">
              {{ direction === 'cold' ? 'cold board' : 'hot board' }}
            </span>
            <span class="label-caps">
              {{ currentMetricLabel.long }} · top {{ Math.min(displayCap, rows.length) }}
            </span>
            <InfoTooltip term="trend_score" size="sm" position="bottom" />
          </div>
          <span v-if="rows.length > displayCap" class="label-caps text-fg-400">
            {{ rows.length - displayCap }} more hidden
          </span>
        </div>

        <!-- Hero grid: 3 columns desktop, 2 tablet, 1 mobile.
             auto-fit lets it gracefully collapse below 6 rows. -->
        <div class="trends-hero-grid mb-6">
          <TrendCard
            v-for="(row, idx) in heroRows"
            :key="`hero-${row.batter.id}`"
            :row="row"
            :metric="metric"
            :metric-label="currentMetricLabel"
            :rank="idx + 1"
          />
        </div>

        <!-- ── Tail section: positions 7+ as compact rows ── -->
        <template v-if="tailRows.length">
          <div class="flex items-baseline gap-3 px-1 mb-3 mt-2">
            <span class="label-bracket text-fg-400">
              continuing
            </span>
            <span class="label-caps text-fg-400">
              ranks {{ HERO_COUNT + 1 }} – {{ HERO_COUNT + tailRows.length }}
            </span>
          </div>

          <div class="flex flex-col gap-1.5">
            <TrendRowCompact
              v-for="(row, idx) in tailRows"
              :key="`tail-${row.batter.id}`"
              :row="row"
              :metric="metric"
              :rank="HERO_COUNT + idx + 1"
            />
          </div>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.cebolla-wordmark-empty {
  display: block;
  width: clamp(180px, 45%, 300px);
  height: auto;
  opacity: 0.55;
  filter: drop-shadow(0 0 8px rgba(255, 42, 42, 0.20));
}

/* ── Hero card grid: top N as bigger feature cards (4×2 = 8) ── */
.trends-hero-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
@media (max-width: 1280px) {
  .trends-hero-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
@media (max-width: 1024px) {
  .trends-hero-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 640px) {
  .trends-hero-grid {
    grid-template-columns: 1fr;
  }
}

/* ── Filter rail ── */
.trends-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  align-items: flex-end;
}
.trends-controls__group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.trends-controls__group-label {
  font-size: 9px;
  display: inline-flex;
  align-items: center;
}
.trends-controls__pills {
  display: flex;
  gap: 0;
  background: rgba(14, 14, 18, 0.6);
  border: 1px solid #1c1c22;
  padding: 2px;
  border-radius: 2px;
  position: relative;
}

/* Tooltip icon hanging off the metric pills cluster — sits 6px to the right */
.trends-controls__pill-info {
  margin-left: 6px;
  align-self: center;
}

/* Tint the "Hot · Cold" label so the header doesn't look monochrome */
.trends-hot-cold-label {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.trends-hot-tint {
  color: rgba(255, 119, 119, 0.75);
}
.trends-cold-tint {
  color: rgba(79, 177, 221, 0.75);
}
.trends-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 10px;
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.04em;
  color: #9B9BA8;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: background-color 120ms ease, color 120ms ease;
  border-radius: 1px;
}
.trends-pill:hover {
  color: #E8E8EE;
  background: rgba(38, 38, 46, 0.4);
}
.trends-pill--active {
  color: #E8E8EE;
  background: rgba(38, 38, 46, 0.7);
  border: 1px solid rgba(255, 42, 42, 0.30);
  margin: -1px;
}
.trends-pill--hot.trends-pill--active {
  border-color: rgba(255, 42, 42, 0.60);
  color: #FFB8B8;
  background: rgba(255, 42, 42, 0.10);
}
.trends-pill--cold.trends-pill--active {
  border-color: rgba(79, 177, 221, 0.50);
  color: #4FB1DD;
  background: rgba(79, 177, 221, 0.08);
}
.trends-pill__icon {
  font-size: 9px;
  opacity: 0.85;
}

/* PA range slider */
.trends-controls__pa {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: rgba(14, 14, 18, 0.6);
  border: 1px solid #1c1c22;
  border-radius: 2px;
  height: 32px;
}
.trends-controls__slider {
  appearance: none;
  -webkit-appearance: none;
  width: 90px;
  height: 2px;
  background: #33333D;
  outline: none;
  cursor: pointer;
}
.trends-controls__slider::-webkit-slider-thumb {
  appearance: none;
  -webkit-appearance: none;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #FF2A2A;
  cursor: pointer;
  box-shadow: 0 0 4px rgba(255, 42, 42, 0.6);
  transition: transform 100ms ease;
}
.trends-controls__slider::-webkit-slider-thumb:hover {
  transform: scale(1.2);
}
.trends-controls__slider::-moz-range-thumb {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #FF2A2A;
  cursor: pointer;
  border: none;
  box-shadow: 0 0 4px rgba(255, 42, 42, 0.6);
}
</style>
