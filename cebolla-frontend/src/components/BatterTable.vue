<script setup>
import { computed, ref } from 'vue'
import { playerHeadshotUrl, hideOnError } from '../utils/mlbImages.js'
import { formatLineupETA } from '../utils/timeHelpers.js'
import { statColor, fmtStat } from '../utils/percentileColors.js'
import { useStatcastBatters } from '../composables/useStatcast.js'
import { useFavorites } from '../composables/useFavorites.js'
import {
  formatScore,
  formatTrend,
  scoreColorClass,
  MIN_PA as CONTACT_MIN_PA,
} from '../composables/useContactScore.js'
import StatcastWindowToggle from './StatcastWindowToggle.vue'
import BatterCard from './BatterCard.vue'
import InfoTooltip from './InfoTooltip.vue'

const { isPlayerFav } = useFavorites()

const props = defineProps({
  lineup:        { type: Array,  required: true },
  odds:          { type: Object, required: true },
  bvp:           { type: Object, required: true },
  projections:   { type: Object, default: () => ({}) },
  pitcherId:     { type: Number, default: null },
  teamLabel:     { type: String, required: true },
  marketMode:    { type: String, default: 'hr' },
  gameId:        { type: Number, default: null },
  gameTimeUtc:   { type: String, default: null },
  batterStats:   { type: Object, default: () => ({}) },
  // Optional snapshot resolver injected from parent (HRReportView) — gives
  // each batter a league-wide contact score + trend. If omitted, the
  // Contact column will render "—" everywhere (graceful no-op).
  getContactSnapshot: { type: Function, default: null },
})
const emit = defineEmits(['log-bet'])

// ── Statcast fetch via composable ───────────────────────────────
const playerIds = computed(() =>
  props.lineup.map(l => l.player?.id).filter(Boolean)
)
const {
  stats: statcastStats,
  windowType,
  setWindow,
  loading: statcastLoading,
} = useStatcastBatters(playerIds, 'l14')

const currentWindow = computed({
  get: () => windowType.value,
  set: (v) => setWindow(v),
})

// ── Contact score lookup ────────────────────────────────────────
// Pool is computed once at parent level and shared across both BatterTable
// instances. Score is L14-anchored: only meaningful when viewing L14 window.
// The trend column will fall back to "L14 only" UI when the user toggles
// to L7/L30/season, keeping the metric honest.
function getContact(batterId) {
  if (windowType.value !== 'l14') return { score: null, trend: null }
  if (!props.getContactSnapshot) return { score: null, trend: null }
  const l14 = statcastStats.value[batterId]
  if (!l14) return { score: null, trend: null }
  return props.getContactSnapshot(batterId, l14)
}

// ── Helpers ─────────────────────────────────────────────────────
function fmtAmerican(n) {
  if (n == null) return null
  return n > 0 ? `+${n}` : `${n}`
}

function getOdds(playerId) {
  const o = props.odds[playerId]
  if (!o) return null
  const marketKey =
    props.marketMode === 'hr' ? 'hr_anytime_yes' :
    props.marketMode === 'hits' ? 'hits_over' :
    'rbi_over'
  return o[marketKey] || null
}

function getProjection(playerId) {
  const marketKey =
    props.marketMode === 'hr'   ? 'hr_anytime' :
    props.marketMode === 'hits' ? 'hits_yes'   :
    null
  if (!marketKey) return null
  const key = `${playerId}_${marketKey}`
  return props.projections[key] || null
}

function hrPctTone(pct) {
  if (pct == null) return 'text-fg-500'
  if (pct >= 5) return 'text-signal-400'
  if (pct >= 3.5) return 'text-signal-200'
  if (pct >= 2) return 'text-fg-600'
  return 'text-edge-cold-1'
}

const lineupETA = computed(() => formatLineupETA(props.gameTimeUtc))

function edgeDisplay(edge) {
  if (edge == null) return { text: '—', cls: 'text-fg-400' }
  const pct = edge * 100
  let cls = 'text-fg-500 bg-bg-200/40'
  if (pct >= 5)       cls = 'text-signal-400 bg-signal-400/15'
  else if (pct >= 2)  cls = 'text-signal-200 bg-signal-400/8'
  else if (pct >= -2) cls = 'text-fg-500 bg-bg-200/40'
  else if (pct >= -5) cls = 'text-edge-cold-2 bg-edge-cold-2/8'
  else                cls = 'text-edge-cold-1 bg-edge-cold-1/15'
  const sign = pct >= 0 ? '+' : ''
  return { text: `${sign}${pct.toFixed(1)}%`, cls }
}

// ── Row construction ────────────────────────────────────────────
const rows = computed(() => {
  return props.lineup.map(l => {
    const player = l.player
    if (!player) return null

    const statcastRow = statcastStats.value[player.id]
    const legacyStats = props.batterStats[player.id] || {}
    const stats = statcastRow || legacyStats

    const o = getOdds(player.id)
    const proj = getProjection(player.id)
    const bvpRow = props.pitcherId
      ? props.bvp[`${player.id}_${props.pitcherId}`]
      : null

    const hrPerPa = stats.hr_per_pa != null ? Number(stats.hr_per_pa) * 100 : null

    // Contact score + trend — null when window != l14, when PA < CONTACT_MIN_PA,
    // when the pool isn't loaded yet, or when stats are missing. Read via the
    // injected snapshot resolver from HRReportView (league-wide pool).
    const cs = getContact(player.id)

    return {
      lineupId: l.id,
      batting_order: l.batting_order,
      position: l.position || player.position,
      bats: l.bats || player.bats,
      player_id: player.id,
      mlbam_id: player.mlbam_id,
      name: player.name,
      pa: stats.pa,
      hr: stats.hr,
      hr_pct: hrPerPa,
      hits_per_pa: stats.hit_per_pa != null ? Number(stats.hit_per_pa) * 100 : null,
      barrel_pct: stats.barrel_pct != null ? Number(stats.barrel_pct) : null,
      hard_hit_pct: stats.hard_hit_pct != null ? Number(stats.hard_hit_pct) : null,
      xba:  stats.xba  != null ? Number(stats.xba)  : null,
      xslg: stats.xslg != null ? Number(stats.xslg) : null,
      odds: o,
      proj,
      bvp: bvpRow,
      contact_score: cs.score,
      contact_trend: cs.trend,
    }
  }).filter(Boolean)
})

// ── Sorting ─────────────────────────────────────────────────────
// Default: edge descending so the value bets surface at the top.
// `null` sortKey === lineup order (no sort).
//
// Click a column header to sort. Click the same header to toggle direction.
// Click # column to reset to lineup order.
//
// Mobile: a small dropdown above the card list exposes the same controls.
const sortKey = ref('edge')         // null | 'odds' | 'proj' | 'edge' | 'bvp' | 'hh' | 'brl' | 'xslg' | 'xba'
const sortDir = ref('desc')         // 'asc' | 'desc'

// Sortable column metadata — single source of truth for headers + the mobile dropdown.
const SORT_COLUMNS = [
  { key: 'odds',    label: 'Odds' },
  { key: 'proj',    label: 'Proj%' },
  { key: 'edge',    label: 'Edge' },
  { key: 'contact', label: 'Contact' },
  { key: 'bvp',     label: 'BvP HR/PA' },
  { key: 'hh',      label: 'HH%' },
  { key: 'brl',     label: 'Brl%' },
  { key: 'xslg',    label: 'xSLG' },
  { key: 'xba',     label: 'xBA' },
]

function sortValue(row, key) {
  if (key === 'odds')    return row.odds?.american_odds ?? null
  if (key === 'proj')    return row.proj?.pct ?? null
  if (key === 'edge')    return row.proj?.edge ?? null
  if (key === 'contact') return row.contact_score ?? null
  if (key === 'bvp')     return row.bvp?.hr_per_pa ?? null
  if (key === 'hh')      return row.hard_hit_pct ?? null
  if (key === 'brl')     return row.barrel_pct ?? null
  if (key === 'xslg')    return row.xslg ?? null
  if (key === 'xba')     return row.xba ?? null
  return null
}

function toggleSort(key) {
  if (sortKey.value === key) {
    // Same column → flip direction
    sortDir.value = sortDir.value === 'desc' ? 'asc' : 'desc'
  } else {
    // New column → default to descending (high values are usually what we care about)
    sortKey.value = key
    sortDir.value = 'desc'
  }
}

function resetSort() {
  sortKey.value = null
  sortDir.value = 'desc'
}

const sortedRows = computed(() => {
  if (!sortKey.value) {
    // No sort → original lineup order
    return rows.value
  }
  const dir = sortDir.value === 'desc' ? -1 : 1
  // Make a shallow copy so we don't mutate the source array
  const arr = rows.value.slice()
  arr.sort((a, b) => {
    const av = sortValue(a, sortKey.value)
    const bv = sortValue(b, sortKey.value)
    // Nulls always go to the bottom, regardless of direction
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    if (av < bv) return -1 * dir
    if (av > bv) return  1 * dir
    return 0
  })
  return arr
})

const isConfirmed = computed(() => {
  return props.lineup.length === 9 &&
         props.lineup.every(l => l.is_confirmed)
})

// Detect if ANY row in the lineup is from the last-known fallback.
// If so, the badge shows "PROJECTED · last lineup" so users know the data
// is from a previous game, not today's posted card.
const lineupSource = computed(() => {
  if (!props.lineup.length) return null
  const sources = new Set(props.lineup.map(l => l.source).filter(Boolean))
  if (sources.has('last_known')) return 'last_known'
  if (sources.has('mlb_api')) return 'mlb_api'
  return null
})

const badgeLabel = computed(() => {
  if (isConfirmed.value) return 'confirmed'
  if (lineupSource.value === 'last_known') return 'projected · last lineup'
  return 'projected'
})
</script>

<template>
  <div class="bg-bg-50 border border-bg-200">
    <!-- Header row -->
    <div class="px-3 sm:px-4 py-3 border-b border-bg-200 flex items-baseline justify-between gap-2 flex-wrap">
      <div class="flex items-baseline gap-2 sm:gap-3 min-w-0">
        <span class="label-bracket text-signal-400 shrink-0">{{ teamLabel }}</span>
        <span class="display-text text-sm sm:text-base text-fg-700 truncate">vs Pitcher</span>
      </div>
      <span
        class="label-caps !text-[9px] px-2 py-0.5 rounded-sm shrink-0"
        :class="isConfirmed
          ? 'text-signal-400 bg-signal-400/10'
          : lineupSource === 'last_known'
            ? 'text-amber-300 bg-amber-500/10'
            : 'text-fg-500 bg-bg-200/60'"
      >
        {{ badgeLabel }}
      </span>
    </div>

    <!-- Statcast window toggle -->
    <div v-if="rows.length" class="px-3 sm:px-4 py-2 border-b border-bg-200 flex items-center justify-between gap-3 flex-wrap">
      <span class="label-caps inline-flex items-center">
        Statcast Window
        <InfoTooltip term="window_l14" />
      </span>
      <div class="flex items-center gap-2">
        <span v-if="statcastLoading" class="label-caps !text-[8px] text-fg-400 italic">loading…</span>
        <StatcastWindowToggle v-model="currentWindow" />
      </div>
    </div>

    <!-- Last-known projection banner -->
    <div
      v-if="rows.length && lineupSource === 'last_known'"
      class="px-3 sm:px-4 py-2 border-b border-bg-200 bg-amber-500/5 flex items-baseline justify-between gap-2 flex-wrap"
    >
      <span class="label-caps !text-[9px] text-amber-300">
        ↺ showing last-known lineup
      </span>
      <span class="text-fg-500 text-[10px]">
        <template v-if="lineupETA">
          Official lineup typically posts <span class="text-fg-700">~{{ lineupETA }}</span>
        </template>
        <template v-else>
          Official lineup typically posts ~3 hours before first pitch
        </template>
      </span>
    </div>

    <!-- Empty state -->
    <div v-if="!rows.length" class="px-4 py-12 text-center">
      <div class="display-text text-lg text-fg-500 italic mb-1">No lineup yet</div>
      <p class="text-fg-500 text-xs">
        <template v-if="lineupETA">
          Lineups typically post around <span class="text-fg-700">{{ lineupETA }}</span>
        </template>
        <template v-else>
          Lineup not posted yet. Check back closer to first pitch.
        </template>
      </p>
    </div>

    <!-- MOBILE VIEW: card-per-batter, hidden md+ -->
    <div v-else class="md:hidden">
      <!-- Mobile sort selector -->
      <div class="px-3 py-2 border-b border-bg-200 flex items-center justify-between gap-2">
        <span class="label-caps !text-[9px] text-fg-500">sort</span>
        <div class="flex items-center gap-1.5">
          <select
            :value="sortKey || ''"
            @change="e => e.target.value ? toggleSort(e.target.value) : resetSort()"
            class="mobile-sort-select"
          >
            <option value="">Lineup order</option>
            <option v-for="c in SORT_COLUMNS" :key="c.key" :value="c.key">{{ c.label }}</option>
          </select>
          <button
            v-if="sortKey"
            type="button"
            class="mobile-sort-dir"
            :title="sortDir === 'desc' ? 'Highest first' : 'Lowest first'"
            @click="sortDir = sortDir === 'desc' ? 'asc' : 'desc'"
          >
            {{ sortDir === 'desc' ? '▼' : '▲' }}
          </button>
        </div>
      </div>
      <BatterCard
        v-for="row in sortedRows"
        :key="row.lineupId"
        :row="row"
        :market-mode="marketMode"
        @log-bet="emit('log-bet', $event)"
      />
    </div>

    <!-- DESKTOP VIEW: existing table, hidden below md -->
    <div v-if="rows.length" class="hidden md:block overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left">
            <th
              class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200 w-8 cursor-pointer hover:text-fg-700 transition"
              :title="sortKey ? 'Reset to lineup order' : 'Lineup order'"
              @click="resetSort"
            >#</th>
            <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Batter</th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'odds' }"
              @click="toggleSort('odds')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                {{ marketMode === 'hr' ? 'HR Odds' : marketMode === 'hits' ? 'Hits O0.5' : 'RBI O0.5' }}
                <span v-if="sortKey === 'odds'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'proj' }"
              @click="toggleSort('proj')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                Proj% <InfoTooltip term="proj_pct" />
                <span v-if="sortKey === 'proj'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'edge' }"
              @click="toggleSort('edge')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                Edge <InfoTooltip term="edge" />
                <span v-if="sortKey === 'edge'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'contact' }"
              @click="toggleSort('contact')"
              :title="`Composite contact score (0-100). Min ${CONTACT_MIN_PA} L14 PA. Built from Brl%/HH%/xSLG percentile-ranked vs all qualified MLB batters.`"
            >
              <span class="inline-flex items-center justify-end gap-1">
                Contact
                <span v-if="sortKey === 'contact'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'bvp' }"
              @click="toggleSort('bvp')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                BvP HR/PA <InfoTooltip term="bvp" />
                <span v-if="sortKey === 'bvp'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'hh' }"
              @click="toggleSort('hh')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                HH% <InfoTooltip term="hard_hit_pct" />
                <span v-if="sortKey === 'hh'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'brl' }"
              @click="toggleSort('brl')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                Brl% <InfoTooltip term="barrel_pct" />
                <span v-if="sortKey === 'brl'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'xslg' }"
              @click="toggleSort('xslg')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                xSLG <InfoTooltip term="xslg" />
                <span v-if="sortKey === 'xslg'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th
              class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right cursor-pointer hover:text-fg-700 transition"
              :class="{ 'text-signal-400': sortKey === 'xba' }"
              @click="toggleSort('xba')"
            >
              <span class="inline-flex items-center justify-end gap-1">
                xBA <InfoTooltip term="xba" />
                <span v-if="sortKey === 'xba'" class="display-num !text-[9px]">{{ sortDir === 'desc' ? '▼' : '▲' }}</span>
              </span>
            </th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-center w-10"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in sortedRows"
            :key="row.lineupId"
            class="group hover:bg-bg-100/50 transition-colors"
          >
            <td class="py-2 px-3 border-b border-bg-200/40 display-num text-xs text-fg-500">
              {{ row.batting_order }}
            </td>
            <td class="py-2 px-3 border-b border-bg-200/40">
              <router-link
                :to="{ name: 'player', params: { playerId: row.player_id } }"
                class="flex items-center gap-2 group-hover:text-signal-200 transition"
              >
                <img
                  v-if="row.mlbam_id"
                  :src="playerHeadshotUrl(row.mlbam_id)"
                  :alt="row.name"
                  class="player-headshot"
                  loading="lazy"
                  @error="hideOnError"
                />
                <span class="text-fg-700 text-sm">{{ row.name }}</span>
                <span
                  v-if="isPlayerFav(row.player_id)"
                  class="fav-row-marker"
                  title="Favorite player"
                  aria-label="Favorite player"
                >★</span>
                <span class="font-mono text-[9px] text-fg-500">{{ row.bats || '?' }}</span>
                <span v-if="row.position"
                      class="font-mono text-[9px] text-fg-400">·{{ row.position }}</span>
              </router-link>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span
                v-if="row.odds"
                class="display-num text-xs px-1.5 py-0.5 rounded-sm font-medium"
                :class="row.odds.american_odds < 0
                  ? 'text-signal-200 bg-signal-400/10'
                  : 'text-fg-700 bg-bg-200/60'"
              >
                {{ fmtAmerican(row.odds.american_odds) }}
              </span>
              <span v-else class="display-num text-xs text-fg-400">—</span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span
                v-if="row.proj?.projected_prob != null"
                class="display-num text-xs"
                :class="hrPctTone(row.proj.projected_prob * 100)"
              >
                {{ (row.proj.projected_prob * 100).toFixed(1) }}
              </span>
              <span
                v-else
                class="display-num text-xs"
                :class="hrPctTone(marketMode === 'hits' ? row.hits_per_pa : row.hr_pct)"
              >
                {{
                  marketMode === 'hits'
                    ? (row.hits_per_pa != null ? row.hits_per_pa.toFixed(1) : '—')
                    : (row.hr_pct != null ? row.hr_pct.toFixed(1) : '—')
                }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <template v-if="row.proj?.edge != null">
                <span
                  class="display-num text-[11px] px-1.5 py-0.5 rounded-sm font-medium"
                  :class="edgeDisplay(row.proj.edge).cls"
                  :title="`Model ${(row.proj.projected_prob*100).toFixed(1)}% vs no-vig ${(row.proj.no_vig_prob*100).toFixed(1)}%`"
                >
                  {{ edgeDisplay(row.proj.edge).text }}
                </span>
              </template>
              <span
                v-else-if="row.proj?.edge_bucket === 'longshot_unrated'"
                class="label-bracket !text-[8px] opacity-40"
                title="Longshot — edge unreliable beyond +2000"
              >
                longshot
              </span>
              <span v-else class="label-bracket !text-[8px] opacity-50">
                {{ marketMode === 'hr' ? 'no data' : 'pending' }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <!-- Contact score: 0-100 composite of Brl%/HH%/xSLG percentile-
                   ranked vs tonight's slate. Null when L14 PA < min, when the
                   user isn't on L14 window, or pool too small. Trend = L14 vs
                   season delta; arrow shown only when |delta| ≥ TREND_THRESHOLD. -->
              <template v-if="row.contact_score != null">
                <span
                  class="display-num text-[11px] font-medium inline-flex items-baseline gap-1 justify-end"
                  :title="`${formatScore(row.contact_score)}/100 contact score (L14 percentile vs all qualified MLB batters)`"
                >
                  <span :class="scoreColorClass(row.contact_score)">{{ formatScore(row.contact_score) }}</span>
                  <span
                    v-if="formatTrend(row.contact_trend).show"
                    class="!text-[9px] font-mono"
                    :class="formatTrend(row.contact_trend).direction === 'up' ? 'text-signal-400' : 'text-edge-cold-1'"
                    :title="`L14 contact is ${formatTrend(row.contact_trend).direction === 'up' ? 'above' : 'below'} season baseline by ${formatTrend(row.contact_trend).magnitude} pts`"
                  >
                    {{ formatTrend(row.contact_trend).direction === 'up' ? '▲' : '▼' }}{{ formatTrend(row.contact_trend).magnitude }}
                  </span>
                </span>
              </template>
              <span
                v-else-if="windowType !== 'l14'"
                class="label-bracket !text-[8px] opacity-40"
                title="Contact score is L14-anchored. Switch window back to L14 to view."
              >L14 only</span>
              <span
                v-else
                class="display-num text-xs text-fg-400"
                :title="`Need at least ${CONTACT_MIN_PA} L14 PA to compute a meaningful score.`"
              >—</span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span v-if="row.bvp" class="display-num text-xs text-fg-600">
                {{ row.bvp.hr }}/{{ row.bvp.pa }}
              </span>
              <span v-else class="display-num text-xs text-fg-400">—</span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span
                class="display-num text-xs"
                :class="statColor(row.hard_hit_pct, 'hard_hit_pct', 'batter')"
              >
                {{ fmtStat(row.hard_hit_pct, 'hard_hit_pct') }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span
                class="display-num text-xs"
                :class="statColor(row.barrel_pct, 'barrel_pct', 'batter')"
              >
                {{ fmtStat(row.barrel_pct, 'barrel_pct') }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span
                class="display-num text-xs"
                :class="statColor(row.xslg, 'xslg', 'batter')"
              >
                {{ fmtStat(row.xslg, 'xslg') }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span
                class="display-num text-xs"
                :class="statColor(row.xba, 'xba', 'batter')"
              >
                {{ fmtStat(row.xba, 'xba') }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-center">
              <button
                @click="emit('log-bet', { player: { id: row.player_id, name: row.name }, proj: row.proj, marketMode })"
                class="log-btn"
                :title="row.odds ? 'Log a bet on this player' : 'Log a bet (no DK odds yet)'"
              >LOG</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* Mobile sort selector — compact native select styled to match the site */
.mobile-sort-select {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: rgba(255, 255, 255, 0.80);
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  padding: 3px 6px;
  outline: none;
  appearance: none;
  -webkit-appearance: none;
  cursor: pointer;
}
.mobile-sort-select:focus {
  border-color: rgba(255, 42, 42, 0.5);
}
.mobile-sort-dir {
  background: rgba(255, 42, 42, 0.10);
  border: 1px solid rgba(255, 42, 42, 0.4);
  color: rgba(255, 42, 42, 0.95);
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  padding: 3px 7px;
  cursor: pointer;
  line-height: 1;
}
.mobile-sort-dir:hover {
  background: rgba(255, 42, 42, 0.18);
}

/* Inline star for favorited players in the row. Subtle gold,
   small enough to not disrupt the table rhythm. */
.fav-row-marker {
  font-size: 10px;
  line-height: 1;
  color: #FFD23F;
  filter: drop-shadow(0 0 2px rgba(255, 210, 63, 0.5));
  user-select: none;
  margin-left: -2px;
}

.player-headshot {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  object-fit: cover;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  filter: grayscale(0.35) brightness(0.95);
  opacity: 0.85;
  transition: filter 0.15s, opacity 0.15s;
}
.group:hover .player-headshot {
  filter: grayscale(0) brightness(1);
  opacity: 1;
}

.log-btn {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  padding: 2px 6px;
  border: 1px solid rgba(255,42,42,0.30);
  background: transparent;
  color: rgba(255,42,42,0.75);
  border-radius: 2px;
  transition: all 0.12s;
}
.log-btn:hover {
  border-color: var(--color-accent-red, #FF2A2A);
  color: var(--color-accent-red, #FF2A2A);
  background: rgba(255,42,42,0.08);
}
</style>
