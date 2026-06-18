<script setup>
/**
 * CalibrationTable.vue — hit-rate × edge_status rollup.
 *
 * Purely presentational; aggregation lives in useShadowLab.calibrationByEdgeStatus
 * (client-side, lock #4). longshot_unrated is already excluded upstream.
 * Compares realized HIT% against AVG MODEL P% to eyeball calibration.
 */
const props = defineProps({
  rows: { type: Array, default: () => [] },
})

function fmtPct(n, dp = 1) {
  if (n == null) return '—'
  return (n * 100).toFixed(dp) + '%'
}
function fmtEdge(n) {
  if (n == null) return '—'
  const pct = n * 100
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}

// Green when realized hit rate meets/exceeds model expectation, signal when
// it materially lags (model over-projected), muted until enough settled.
function hitColor(row) {
  if (row.hit_rate == null || row.avg_model_prob == null) return 'text-fg-500'
  const diff = row.hit_rate - row.avg_model_prob
  if (diff >= -0.02) return 'text-emerald-400'
  return 'text-signal-400'
}
</script>

<template>
  <div v-if="!rows.length" class="text-fg-400 text-xs italic py-6 text-center border border-bg-200/30 rounded-sm">
    Sin datos settleados todavía — vuelve después de los partidos.
  </div>
  <div v-else class="overflow-x-auto border border-bg-200/40 rounded-sm">
    <table class="w-full text-sm">
      <thead>
        <tr class="border-b border-bg-200/60 text-fg-300 font-mono uppercase tracking-wide2 !text-[9px]">
          <th class="text-left py-2 px-3">EDGE STATUS</th>
          <th class="text-right py-2 px-3">N</th>
          <th class="text-right py-2 px-3">SETTLED</th>
          <th class="text-right py-2 px-3">HR</th>
          <th class="text-right py-2 px-3">HIT %</th>
          <th class="text-right py-2 px-3">AVG MODEL P%</th>
          <th class="text-right py-2 px-3">AVG EDGE</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.edge_status" class="border-b border-bg-200/30">
          <td class="py-2 px-3 text-fg-200">{{ r.edge_status }}</td>
          <td class="py-2 px-3 text-right display-num text-fg-300">{{ r.n }}</td>
          <td class="py-2 px-3 text-right display-num text-fg-300">{{ r.settled }}</td>
          <td class="py-2 px-3 text-right display-num text-fg-300">{{ r.hr }}</td>
          <td class="py-2 px-3 text-right display-num" :class="hitColor(r)">{{ fmtPct(r.hit_rate) }}</td>
          <td class="py-2 px-3 text-right display-num text-fg-400">{{ fmtPct(r.avg_model_prob) }}</td>
          <td class="py-2 px-3 text-right display-num"
              :class="r.avg_edge == null ? 'text-fg-500' : r.avg_edge >= 0 ? 'text-emerald-400' : 'text-signal-400'">
            {{ fmtEdge(r.avg_edge) }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
