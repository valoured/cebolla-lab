<script setup>
/**
 * PickDetailDrawer.vue — right slide-over showing one pick's full breakdown.
 *
 * Renders the row object passed in (no fetching). Mirrors SearchModal.vue's
 * Teleport + Escape + body-scroll-lock pattern. @close clears selectedPick
 * in ShadowLab.vue.
 */
import { computed, watch, onUnmounted } from 'vue'

const props = defineProps({
  pick: { type: Object, default: null },
})
const emit = defineEmits(['close'])

const visible = computed(() => !!props.pick)

function fmtPct(n, dp = 1) {
  if (n == null) return '—'
  return (Number(n) * 100).toFixed(dp) + '%'
}
function fmtNum(n, dp = 3) {
  if (n == null) return '—'
  return Number(n).toFixed(dp)
}
function fmtEdge(e) {
  if (e == null) return '—'
  const pct = Number(e) * 100
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}
function fmtOdds(n) {
  if (n == null) return '—'
  return (n >= 0 ? '+' : '') + n
}

const OUTCOME_LABEL = {
  hr: 'HR', no_hr: 'NO HR', did_not_play: 'DID NOT PLAY',
  game_void: 'GAME VOID', pending: 'PENDING',
}

// components JSONB → ordered factor rows for the COMPONENTS section
const componentRows = computed(() => {
  const c = props.pick?.components || {}
  return [
    ['Shrunk obs HR/PA', fmtNum(c.shrunk_observed_hr_per_pa, 5)],
    ['Batter factor',    fmtNum(c.batter_profile_factor, 4)],
    ['Pitcher factor',   fmtNum(c.pitcher_factor, 4)],
    ['Park mult',        `${fmtNum(c.park_mult, 4)} (idx ${c.park_index ?? '—'})`],
    ['Weather mult',     fmtNum(c.weather_mult, 4)],
    ['Combined factor',  fmtNum(c.combined_factor, 4)],
    ['Expected PA',      fmtNum(c.expected_pas, 2)],
  ]
})

const zRows = computed(() => {
  const z = props.pick?.components?.feature_zs || {}
  return Object.entries(z).map(([k, v]) => [k, fmtNum(v, 4)])
})

const activeWarnings = computed(() => {
  const w = props.pick?.warnings || {}
  return Object.keys(w).filter(k => w[k] === true)
})

// Projected-lineup provenance: source label + the history window it was built
// from. lineup_source lives on the row; the date range on warnings.lineup_window.
const isProjected = computed(() => props.pick?.lineup_source === 'projected')
const lineupWindow = computed(() => props.pick?.warnings?.lineup_window || null)

function onKeydown(e) {
  if (e.key === 'Escape' && visible.value) emit('close')
}

// Attach the key listener only while a pick is open; clean up on close/unmount.
watch(visible, (open) => {
  if (typeof document === 'undefined') return
  if (open) document.addEventListener('keydown', onKeydown)
  else document.removeEventListener('keydown', onKeydown)
})
onUnmounted(() => {
  if (typeof document !== 'undefined') document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <Teleport to="body">
    <transition name="drawer-fade">
      <div v-if="pick" class="drawer-overlay" @click.self="$emit('close')">
        <aside class="drawer-panel">
          <!-- Header -->
          <div class="drawer-head">
            <div>
              <div class="label-bracket text-signal-400">{{ pick.away_abbrev }}@{{ pick.home_abbrev }}</div>
              <h2 class="font-display text-xl text-fg-700 mt-0.5">{{ pick.player_name }}</h2>
              <div class="flex items-center gap-2 mt-1">
                <span class="label-bracket text-fg-400">{{ pick.edge_status || '—' }}</span>
                <span class="label-bracket text-fg-500">{{ OUTCOME_LABEL[pick.outcome_status] || '—' }}</span>
              </div>
            </div>
            <button type="button" class="drawer-close" aria-label="Close" @click="$emit('close')">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>

          <!-- Model -->
          <section class="drawer-section">
            <div class="label-bracket text-signal-400 mb-2">model</div>
            <dl class="kv">
              <div><dt>Per-PA</dt><dd>{{ fmtPct(pick.model_prob_per_pa, 3) }}</dd></div>
              <div><dt>Per-game</dt><dd>{{ fmtPct(pick.model_prob_per_game) }}</dd></div>
              <div><dt>No-vig</dt><dd>{{ fmtPct(pick.no_vig_prob) }}</dd></div>
              <div><dt>Edge</dt><dd>{{ fmtEdge(pick.edge_pct) }}</dd></div>
              <div><dt>Book odds</dt><dd>{{ fmtOdds(pick.best_american_odds) }}</dd></div>
              <div><dt>Model</dt><dd>{{ pick.model_version }}</dd></div>
            </dl>
          </section>

          <!-- Components -->
          <section class="drawer-section">
            <div class="label-bracket text-signal-400 mb-2">components</div>
            <dl class="kv">
              <div v-for="[k, v] in componentRows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></div>
            </dl>
          </section>

          <!-- Feature z-scores -->
          <section v-if="zRows.length" class="drawer-section">
            <div class="label-bracket text-signal-400 mb-2">feature z-scores</div>
            <dl class="kv">
              <div v-for="[k, v] in zRows" :key="k"><dt>{{ k }}</dt><dd>{{ v }}</dd></div>
            </dl>
          </section>

          <!-- Lineup provenance -->
          <section v-if="pick.lineup_source" class="drawer-section">
            <div class="label-bracket text-signal-400 mb-2">lineup</div>
            <dl class="kv">
              <div><dt>Source</dt><dd>{{ isProjected ? 'projected' : pick.lineup_source }}</dd></div>
            </dl>
            <p v-if="isProjected" class="text-fg-500 text-xs italic mt-1.5">
              Projected lineup used<template v-if="lineupWindow">
              ({{ lineupWindow.from }} → {{ lineupWindow.to }}, {{ lineupWindow.days }}-day typical 9)</template>.
            </p>
          </section>

          <!-- Warnings -->
          <section class="drawer-section">
            <div class="label-bracket text-signal-400 mb-2">warnings</div>
            <div v-if="!activeWarnings.length" class="text-fg-500 text-xs italic">Ninguna.</div>
            <div v-else class="flex flex-wrap gap-1.5">
              <span v-for="w in activeWarnings" :key="w" class="warn-badge">{{ w }}</span>
            </div>
          </section>
        </aside>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(8, 8, 10, 0.72);
  backdrop-filter: blur(4px);
  display: flex;
  justify-content: flex-end;
  z-index: 9000;
}
.drawer-panel {
  width: 100%;
  max-width: 440px;
  height: 100%;
  overflow-y: auto;
  background: rgba(8, 8, 10, 0.98);
  border-left: 1px solid rgba(255, 42, 42, 0.30);
  box-shadow: -12px 0 60px rgba(0, 0, 0, 0.7);
  padding: 18px 18px calc(18px + env(safe-area-inset-bottom));
}
.drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
.drawer-close {
  width: 36px; height: 36px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid rgba(255, 255, 255, 0.10);
  background: transparent; color: rgba(255, 255, 255, 0.55);
  border-radius: 2px; cursor: pointer; flex-shrink: 0;
  transition: color 120ms ease, border-color 120ms ease;
}
.drawer-close:hover { color: #fff; border-color: #FF2A2A; }
.drawer-section { padding: 14px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); }
.kv { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }
.kv > div { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.kv dt {
  font-family: 'IBM Plex Sans', sans-serif; font-size: 11px;
  color: rgb(155, 155, 168);
}
.kv dd {
  font-family: 'JetBrains Mono', monospace; font-size: 12px;
  font-variant-numeric: tabular-nums; color: rgb(232, 232, 238);
}
.warn-badge {
  font-family: 'JetBrains Mono', monospace; font-size: 9px;
  letter-spacing: 0.05em; text-transform: uppercase; padding: 3px 7px;
  border: 1px solid rgba(255, 200, 80, 0.40); color: rgba(255, 200, 80, 0.9);
  border-radius: 2px;
}
.drawer-fade-enter-active, .drawer-fade-leave-active { transition: opacity 160ms ease; }
.drawer-fade-enter-active .drawer-panel, .drawer-fade-leave-active .drawer-panel { transition: transform 160ms ease; }
.drawer-fade-enter-from, .drawer-fade-leave-to { opacity: 0; }
.drawer-fade-enter-from .drawer-panel, .drawer-fade-leave-to .drawer-panel { transform: translateX(24px); }
</style>
