<script setup>
/**
 * ShadowLab.vue — v2 shadow validation surface (/shadow, URL-only).
 *
 * Reads picks_v2_enriched via useShadowLab. Static refresh only (no realtime).
 * All filtering is client-side over the already-fetched today's picks; the
 * window toggle re-fetches only the calibration set. Tracking only — NOT betting.
 */
import { ref, computed, onMounted } from 'vue'
import { useShadowLab } from '../composables/useShadowLab'
import PicksTable from '../components/shadow/PicksTable.vue'
import PickDetailDrawer from '../components/shadow/PickDetailDrawer.vue'
import CalibrationTable from '../components/shadow/CalibrationTable.vue'
import WindowToggle from '../components/shadow/WindowToggle.vue'
import EdgeStatusFilter from '../components/shadow/EdgeStatusFilter.vue'
import WarningsFilter from '../components/shadow/WarningsFilter.vue'

const {
  today, loading, initialized, refresh,
  calibrationByEdgeStatus, calibWindow, setCalibWindow,
} = useShadowLab()

// ── Local UI state (all client-side) ──
const edgeFilter = ref(new Set())          // empty = all
const warningFilters = ref({
  weather_fallback: false, did_not_play: false,
  longshot: false, per_game_high: false, is_fallback: false,
})
const showLongshots = ref(false)           // hide longshot_unrated by default (lock #5)
const selectedPick = ref(null)             // drives the drawer

onMounted(refresh)

const filteredPicks = computed(() => {
  let rows = today.value
  if (!showLongshots.value) rows = rows.filter(r => r.edge_status !== 'longshot_unrated')
  if (edgeFilter.value.size) rows = rows.filter(r => edgeFilter.value.has(r.edge_status))
  for (const key of Object.keys(warningFilters.value)) {
    if (warningFilters.value[key]) rows = rows.filter(r => (r.warnings || {})[key] === true)
  }
  return rows
})

// Count hidden longshots for the toggle hint.
const longshotCount = computed(() =>
  today.value.filter(r => r.edge_status === 'longshot_unrated').length)
</script>

<template>
  <div class="shadow-lab py-6 px-4 max-w-[1280px] mx-auto">
    <!-- 1. Persistent shadow banner -->
    <div class="shadow-banner mb-5">
      <span class="shadow-banner__icon">⚠</span>
      <span class="shadow-banner__text">SHADOW VALIDATION — TRACKING ONLY, NOT BETTING</span>
    </div>

    <!-- 2. Header -->
    <div class="mb-6">
      <div class="label-bracket text-signal-400 mb-1">[ MODEL V2.0 / SHADOW ]</div>
      <h1 class="font-display text-2xl text-fg-700">Shadow Lab</h1>
      <p class="text-fg-400 text-sm mt-1">
        Full-slate hr_v2.0 picks tracked against book outcomes — no stake. Validating
        calibration before the model ever places a dollar.
      </p>
    </div>

    <!-- 3. Today's Picks -->
    <div class="mb-3 flex items-center justify-between gap-3">
      <div class="label-bracket text-signal-400">[ TODAY'S PICKS ]</div>
      <button type="button" class="refresh-btn" @click="refresh">[ refresh ]</button>
    </div>
    <div class="flex flex-col gap-2 mb-3">
      <EdgeStatusFilter v-model="edgeFilter" />
      <WarningsFilter
        v-model:filters="warningFilters"
        v-model:showLongshots="showLongshots"
      />
      <div v-if="!showLongshots && longshotCount" class="text-fg-500 text-[11px] italic">
        {{ longshotCount }} longshot_unrated pick(s) hidden — toggle "Longshots" to show.
      </div>
    </div>
    <PicksTable
      :picks="filteredPicks"
      :loading="loading && !initialized"
      @select="selectedPick = $event"
    />
    <div class="text-fg-500 text-[11px] mt-2 display-num">
      {{ filteredPicks.length }} shown · {{ today.length }} total today
    </div>

    <!-- 4. Calibration rollup -->
    <div class="mt-8 mb-3 flex items-center justify-between gap-3">
      <div class="label-bracket text-signal-400">[ CALIBRATION ROLLUP ]</div>
      <WindowToggle :modelValue="calibWindow" @update:modelValue="setCalibWindow" />
    </div>
    <CalibrationTable :rows="calibrationByEdgeStatus" />
    <p class="text-fg-500 text-[11px] italic mt-2">
      Hit % vs avg model P% by edge bucket. longshot_unrated excluded. HIT% denominator
      counts settled picks only (HR / NO-HR).
    </p>

    <!-- Detail drawer -->
    <PickDetailDrawer :pick="selectedPick" @close="selectedPick = null" />
  </div>
</template>

<style scoped>
.shadow-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid #FF2A2A;
  background: rgba(255, 42, 42, 0.08);
  border-radius: 2px;
}
.shadow-banner__icon { color: #FF2A2A; font-size: 14px; }
.shadow-banner__text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #FF6B6B;
}
.refresh-btn {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 6px 10px;
  min-height: 34px;
  border: 1px solid rgba(255, 255, 255, 0.10);
  color: rgb(155, 155, 168);
  background: transparent;
  border-radius: 2px;
  transition: all 0.15s;
}
.refresh-btn:hover { color: #FF2A2A; border-color: #FF2A2A; }
</style>
