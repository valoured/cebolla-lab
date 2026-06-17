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
const howToReadOpen = ref(false)           // "How to read this" — collapsed by default

// Illustrative Day-1 (2026-06-17) calibration snapshot for the explainer.
// Static copy — NOT live data; the real numbers live in the Calibration Rollup.
const HOW_TO_CALIB = [
  { bucket: '20%+ HR',   hit: '~0%',  note: 'phantom edges' },
  { bucket: '15-20% HR', hit: '~6%',  note: 'still overrating' },
  { bucket: '10-15% HR', hit: '~20%', note: 'reasonable' },
  { bucket: '5-10% HR',  hit: '~9%',  note: 'reasonable' },
]

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

    <!-- How to read this (collapsible, collapsed by default) -->
    <div class="htr mb-6">
      <button
        type="button"
        class="htr-toggle"
        :aria-expanded="howToReadOpen"
        @click="howToReadOpen = !howToReadOpen"
      >
        <span class="label-bracket">HOW TO READ THIS</span>
        <span class="htr-caret" :class="{ open: howToReadOpen }">▾</span>
      </button>

      <transition name="htr-collapse">
        <div v-if="howToReadOpen" class="htr-body">
          <p class="htr-lede">
            <span class="htr-warn-icon">⚠️</span>
            Shadow Lab is research infrastructure, not a picks service. Use this surface
            to study how the v2 model behaves before trusting it with real money.
          </p>

          <div class="htr-block">
            <div class="htr-head">What you're looking at</div>
            <p>
              Every confirmed-lineup batter on today's slate gets a v2 HR probability
              prediction. We compare it to the book's no-vig implied probability and
              compute the edge. Picks with the biggest edges land at the top.
            </p>
          </div>

          <!-- Emphasized warning with left-border accent -->
          <div class="htr-warn">
            <div class="htr-head htr-head--warn">Do not bet blindly from this page</div>
            <p>
              The v2 model is in shadow validation — accumulating data to determine if its
              predictions are calibrated. Early data (Day 1 = 2026-06-17) suggests the model
              is overconfident on its top picks:
            </p>
            <table class="htr-mini">
              <thead>
                <tr><th class="text-left">Model bucket</th><th class="text-left">Actual hit rate</th></tr>
              </thead>
              <tbody>
                <tr v-for="r in HOW_TO_CALIB" :key="r.bucket">
                  <td class="display-num">{{ r.bucket }}</td>
                  <td><span class="display-num htr-hit">{{ r.hit }}</span>
                    <span class="htr-note">← {{ r.note }}</span></td>
                </tr>
              </tbody>
            </table>
            <p class="htr-warn-foot">
              The strongest "strong_back" picks (biggest model edges) are HIGHEST risk for
              phantom edge — the book is likely pricing them correctly.
            </p>
          </div>

          <div class="htr-block">
            <div class="htr-head">When would this become betable?</div>
            <p>
              After 200+ settled picks (~7-10 days of data), we'll know if any subset of the
              model is reliably calibrated. Until then, treat every pick as untested.
            </p>
          </div>

          <div class="htr-block">
            <div class="htr-head">What each field means</div>
            <ul class="htr-fields">
              <li><code>ODDS</code> — best book price (American)</li>
              <li><code>MODEL P%</code> — v2's predicted HR probability for the full game</li>
              <li><code>EDGE</code> — model_prob minus no-vig book implied</li>
              <li><code>STATUS</code> — strong_back (≥+5% edge), lean_back (+2-5%), flat,
                lean_fade (-5 to -2%), strong_fade (≤-5%)</li>
              <li><code>OUTCOME</code> — HR / NO HR / DNP (did not play) / VOID (game
                postponed) / pending</li>
              <li><code>⚠ FLAGS</code>:
                <ul class="htr-flags">
                  <li><code>WX</code> = weather data was missing, fallback applied</li>
                  <li><code>DNP</code> = batter didn't play (scratched / 0-PA sub)</li>
                  <li><code>HIGH</code> = model probability over 20% (rare elites)</li>
                  <li><code>PFB</code> = pitcher data partial (league avg used)</li>
                </ul>
              </li>
            </ul>
          </div>

          <div class="htr-block">
            <div class="htr-head">Calibration rollup (below)</div>
            <p>
              Compares the model's claimed probability to actual hit rate by edge_status. A
              working model would show HIT% roughly matching AVG MODEL P%. Big gaps = model
              needs retuning.
            </p>
          </div>
        </div>
      </transition>
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

/* ── How to read this (collapsible) ── */
.htr {
  border: 1px solid var(--bg-200, #26262E);
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.015);
}
.htr-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 11px 14px;
  background: transparent;
  border: none;
  cursor: pointer;
  color: #FF2A2A;
  min-height: 44px;
}
.htr-toggle:hover { background: rgba(255, 42, 42, 0.04); }
.htr-toggle .label-bracket { color: #FF2A2A; }
.htr-caret {
  color: #FF2A2A;
  font-size: 11px;
  transition: transform 160ms ease;
}
.htr-caret.open { transform: rotate(180deg); }

.htr-body {
  padding: 4px 16px 18px;
  border-top: 1px solid var(--bg-200, #26262E);
  font-family: 'IBM Plex Sans', sans-serif;
  font-size: 13px;
  line-height: 1.6;
  color: rgb(155, 155, 168);   /* fg-500 */
}
.htr-body p { margin: 6px 0; }

.htr-lede {
  margin-top: 12px !important;
  color: rgb(198, 198, 208);   /* fg-600 */
}
.htr-warn-icon { margin-right: 4px; }

.htr-block { margin-top: 16px; }
.htr-head {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: rgb(232, 232, 238);   /* fg-700 */
  margin-bottom: 4px;
}

/* Emphasized "do not bet blindly" block with left-border accent */
.htr-warn {
  margin-top: 16px;
  padding: 10px 14px;
  border-left: 3px solid #FF2A2A;
  background: rgba(255, 42, 42, 0.06);
  border-radius: 0 2px 2px 0;
}
.htr-head--warn { color: #FF6B6B; font-weight: 700; }
.htr-warn-foot { color: rgb(198, 198, 208); font-weight: 600; }

.htr-mini {
  margin: 10px 0;
  border-collapse: collapse;
}
.htr-mini th {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: rgb(155, 155, 168);
  padding: 3px 18px 5px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.10);
}
.htr-mini td {
  font-size: 12px;
  padding: 3px 18px 3px 0;
  color: rgb(198, 198, 208);
}
.htr-mini .display-num {
  font-variant-numeric: tabular-nums lining-nums;
}
.htr-hit { color: rgb(232, 232, 238); }
.htr-note {
  font-size: 11px;
  color: rgb(155, 155, 168);
  margin-left: 6px;
}

.htr-fields { margin: 6px 0; padding: 0; list-style: none; }
.htr-fields > li { margin: 5px 0; }
.htr-fields code,
.htr-flags code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #FF6B6B;
  background: rgba(255, 42, 42, 0.06);
  padding: 1px 5px;
  border-radius: 2px;
  margin-right: 2px;
}
.htr-flags {
  margin: 4px 0 4px 14px;
  padding: 0;
  list-style: none;
}
.htr-flags > li { margin: 3px 0; }

/* Collapse transition */
.htr-collapse-enter-active, .htr-collapse-leave-active {
  transition: opacity 160ms ease;
}
.htr-collapse-enter-from, .htr-collapse-leave-to { opacity: 0; }
</style>
