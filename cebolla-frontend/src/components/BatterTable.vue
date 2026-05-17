<script setup>
import { computed } from 'vue'

const props = defineProps({
  lineup:      { type: Array,  required: true },
  batterStats: { type: Object, required: true },
  odds:        { type: Object, required: true },
  bvp:         { type: Object, required: true },
  projections: { type: Object, default: () => ({}) },   // new
  pitcherId:   { type: Number, default: null },
  teamLabel:   { type: String, required: true },
  marketMode:  { type: String, default: 'hr' },
  gameId:      { type: Number, default: null },
})
const emit = defineEmits(['log-bet'])

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
  if (props.marketMode !== 'hr') return null   // only HR projections for now
  const key = `${playerId}_hr_anytime`
  return props.projections[key] || null
}

function hrPctTone(pct) {
  if (pct == null) return 'text-fg-500'
  if (pct >= 5) return 'text-signal-400'
  if (pct >= 3.5) return 'text-signal-200'
  if (pct >= 2) return 'text-fg-600'
  return 'text-edge-cold-1'
}

// Edge formatting: returns pill class + display text
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

const rows = computed(() => {
  return props.lineup.map(l => {
    const player = l.player
    if (!player) return null
    const stats = props.batterStats[player.id] || {}
    const o = getOdds(player.id)
    const proj = getProjection(player.id)
    const bvpRow = props.pitcherId
      ? props.bvp[`${player.id}_${props.pitcherId}`]
      : null

    const hrPerPa = stats.hr_per_pa != null ? Number(stats.hr_per_pa) * 100 : null

    return {
      lineupId: l.id,
      batting_order: l.batting_order,
      position: l.position || player.position,
      bats: l.bats || player.bats,
      player_id: player.id,
      name: player.name,
      pa: stats.pa,
      hr: stats.hr,
      hr_pct: hrPerPa,
      hits_per_pa: stats.hit_per_pa != null ? Number(stats.hit_per_pa) * 100 : null,
      barrel_pct: stats.barrel_pct,
      hard_hit_pct: stats.hard_hit_pct,
      ev_avg: stats.ev_avg,
      odds: o,
      proj,
      bvp: bvpRow,
    }
  }).filter(Boolean)
})

const isConfirmed = computed(() => {
  return props.lineup.length === 9 &&
         props.lineup.every(l => l.is_confirmed)
})
</script>

<template>
  <div class="bg-bg-50 border border-bg-200">
    <div class="px-4 py-3 border-b border-bg-200 flex items-baseline justify-between gap-2">
      <div class="flex items-baseline gap-3">
        <span class="label-bracket text-signal-400">{{ teamLabel }}</span>
        <span class="display-text text-base text-fg-700">vs Pitcher</span>
      </div>
      <span
        class="label-caps !text-[9px] px-2 py-0.5 rounded-sm"
        :class="isConfirmed
          ? 'text-signal-400 bg-signal-400/10'
          : 'text-fg-500 bg-bg-200/60'"
      >
        {{ isConfirmed ? 'confirmed' : 'projected' }}
      </span>
    </div>

    <div v-if="!rows.length" class="px-4 py-12 text-center">
      <div class="display-text text-lg text-fg-500 italic mb-1">Sin alineación</div>
      <p class="text-fg-500 text-xs">Lineup not posted yet. Check back closer to first pitch.</p>
    </div>

    <div v-else class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left">
            <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200 w-8">#</th>
            <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Batter</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">
              {{ marketMode === 'hr' ? 'HR Odds' : marketMode === 'hits' ? 'Hits O0.5' : 'RBI O0.5' }}
            </th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Proj%</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Edge</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">BvP HR/PA</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Brl%</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">HH%</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">EV</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-center w-10"></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.lineupId"
            class="group hover:bg-bg-100/50 transition-colors"
          >
            <td class="py-2 px-3 border-b border-bg-200/40 display-num text-xs text-fg-500">
              {{ row.batting_order }}
            </td>
            <td class="py-2 px-3 border-b border-bg-200/40">
              <router-link
                :to="{ name: 'player', params: { playerId: row.player_id } }"
                class="flex items-baseline gap-2 group-hover:text-signal-200 transition"
              >
                <span class="text-fg-700 text-sm">{{ row.name }}</span>
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
            <!-- Projected probability -->
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
            <!-- Edge (real value when available) -->
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
              <span v-if="row.bvp" class="display-num text-xs text-fg-600">
                {{ row.bvp.hr }}/{{ row.bvp.pa }}
              </span>
              <span v-else class="display-num text-xs text-fg-400">—</span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span class="display-num text-xs text-fg-600">
                {{ row.barrel_pct != null ? Number(row.barrel_pct).toFixed(1) : '—' }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span class="display-num text-xs text-fg-600">
                {{ row.hard_hit_pct != null ? Number(row.hard_hit_pct).toFixed(1) : '—' }}
              </span>
            </td>
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span class="display-num text-xs text-fg-600">
                {{ row.ev_avg != null ? Number(row.ev_avg).toFixed(1) : '—' }}
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
