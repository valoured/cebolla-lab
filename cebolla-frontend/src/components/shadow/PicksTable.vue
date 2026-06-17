<script setup>
/**
 * PicksTable.vue — today's shadow picks (presentational).
 *
 * No fetching. Receives already-filtered rows; row click emits 'select'
 * (ShadowLab owns selectedPick → PickDetailDrawer).
 */
const props = defineProps({
  picks: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})
defineEmits(['select'])

function fmtPct(n, dp = 1) {
  if (n == null) return '—'
  return (Number(n) * 100).toFixed(dp) + '%'
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
function edgeColor(e) {
  if (e == null) return 'text-fg-500'
  return Number(e) >= 0 ? 'text-emerald-400' : 'text-signal-400'
}

const EDGE_PILL = {
  strong_back: 'text-emerald-400 border-emerald-400/40',
  lean_back:   'text-emerald-300 border-emerald-300/30',
  flat:        'text-fg-400 border-bg-300',
  lean_fade:   'text-signal-300 border-signal-300/30',
  strong_fade: 'text-signal-400 border-signal-400/40',
  longshot_unrated: 'text-fg-500 border-bg-300',
}
function edgePill(status) {
  return EDGE_PILL[status] || 'text-fg-400 border-bg-300'
}

// outcome_status → row text tint + label
const OUTCOME = {
  hr:           { cls: 'text-emerald-400', label: 'HR' },
  no_hr:        { cls: 'text-signal-400',  label: 'NO HR' },
  did_not_play: { cls: 'text-fg-400',      label: 'DNP' },
  game_void:    { cls: 'text-fg-400',      label: 'VOID' },
  pending:      { cls: 'text-fg-500',      label: '—' },
}
function outcome(status) {
  return OUTCOME[status] || OUTCOME.pending
}

// warnings JSONB → short chips for the ⚠ column
const WARN_LABEL = {
  weather_fallback: 'wx',
  did_not_play: 'dnp',
  longshot: 'ls',
  per_game_high: 'high',
  is_fallback: 'pfb',
}
function warningChips(warnings) {
  if (!warnings) return []
  return Object.keys(WARN_LABEL).filter(k => warnings[k] === true).map(k => WARN_LABEL[k])
}
</script>

<template>
  <div v-if="loading" class="text-fg-400 text-sm italic py-8 text-center">
    Cargando…
  </div>
  <div v-else-if="!picks.length"
       class="text-fg-400 text-sm italic py-8 text-center border border-bg-200/30 rounded-sm">
    Sin picks — ajusta los filtros o vuelve cuando se confirmen las alineaciones.
  </div>
  <div v-else class="overflow-x-auto border border-bg-200/40 rounded-sm">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-bg-200/60 text-fg-300 label-bracket !text-[9px]">
          <th class="text-left py-2 px-3">PLAYER</th>
          <th class="text-left py-2 px-3">MATCHUP</th>
          <th class="text-right py-2 px-3">ODDS</th>
          <th class="text-right py-2 px-3">MODEL P%</th>
          <th class="text-right py-2 px-3">EDGE</th>
          <th class="text-left py-2 px-3">STATUS</th>
          <th class="text-center py-2 px-3">OUTCOME</th>
          <th class="text-left py-2 px-3">⚠</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="p in picks"
          :key="p.id"
          class="border-b border-bg-200/30 hover:bg-bg-100/30 cursor-pointer"
          @click="$emit('select', p)"
        >
          <td class="py-2 px-3 text-fg-100">{{ p.player_name }}</td>
          <td class="py-2 px-3 text-fg-500 text-xs display-num">
            {{ p.away_abbrev }}@{{ p.home_abbrev }}
          </td>
          <td class="py-2 px-3 text-right display-num text-fg-200">{{ fmtOdds(p.best_american_odds) }}</td>
          <td class="py-2 px-3 text-right display-num text-fg-200">{{ fmtPct(p.model_prob_per_game) }}</td>
          <td class="py-2 px-3 text-right display-num" :class="edgeColor(p.edge_pct)">{{ fmtEdge(p.edge_pct) }}</td>
          <td class="py-2 px-3">
            <span class="edge-status-pill" :class="edgePill(p.edge_status)">{{ p.edge_status || '—' }}</span>
          </td>
          <td class="py-2 px-3 text-center">
            <span class="label-bracket text-[10px]" :class="outcome(p.outcome_status).cls">
              {{ outcome(p.outcome_status).label }}
            </span>
          </td>
          <td class="py-2 px-3">
            <span v-for="w in warningChips(p.warnings)" :key="w" class="warn-chip">{{ w }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.edge-status-pill {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  padding: 2px 6px;
  border: 1px solid;
  border-radius: 2px;
}
.warn-chip {
  display: inline-block;
  font-family: 'JetBrains Mono', monospace;
  font-size: 8px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 1px 4px;
  margin-right: 3px;
  border: 1px solid rgba(255, 200, 80, 0.35);
  color: rgba(255, 200, 80, 0.85);
  border-radius: 2px;
}
</style>
