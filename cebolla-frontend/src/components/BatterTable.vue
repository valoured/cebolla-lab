<script setup>
import { computed } from 'vue'

const props = defineProps({
  lineup:      { type: Array,  required: true },   // [{batting_order, player, bats, ...}]
  batterStats: { type: Object, required: true },   // {player_id: row}
  odds:        { type: Object, required: true },   // {player_id: {market: row}}
  bvp:         { type: Object, required: true },   // {`${b}_${p}`: row}
  pitcherId:   { type: Number, default: null },    // opposing pitcher
  teamLabel:   { type: String, required: true },
  marketMode:  { type: String, default: 'hr' },    // 'hr' | 'hits' | 'rbi'
})

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

// Color tone for HR% based on intensity
function hrPctTone(pct) {
  if (pct == null) return 'text-fg-500'
  if (pct >= 5) return 'text-signal-400'
  if (pct >= 3.5) return 'text-signal-200'
  if (pct >= 2) return 'text-fg-600'
  return 'text-edge-cold-1'
}

const rows = computed(() => {
  return props.lineup.map(l => {
    const player = l.player
    if (!player) return null
    const stats = props.batterStats[player.id] || {}
    const o = getOdds(player.id)
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
      barrel_pct: stats.barrel_pct,
      hard_hit_pct: stats.hard_hit_pct,
      ev_avg: stats.ev_avg,
      hits: stats.hits,
      hits_per_pa: stats.hit_per_pa != null ? Number(stats.hit_per_pa) * 100 : null,
      odds: o,
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
    <!-- Header -->
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

    <!-- Empty state -->
    <div v-if="!rows.length" class="px-4 py-12 text-center">
      <div class="display-text text-lg text-fg-500 italic mb-1">Sin alineación</div>
      <p class="text-fg-500 text-xs">Lineup not posted yet. Check back closer to first pitch.</p>
    </div>

    <!-- Table -->
    <div v-else class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="text-left">
            <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200 w-8">#</th>
            <th class="label-caps !text-[8px] py-2 px-3 border-b border-bg-200">Batter</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">
              {{ marketMode === 'hr' ? 'HR Odds' : marketMode === 'hits' ? 'Hits O0.5' : 'RBI O0.5' }}
            </th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">
              {{ marketMode === 'hr' ? 'HR%' : marketMode === 'hits' ? 'H%' : 'HR%' }}
            </th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Edge</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">BvP HR/PA</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">Brl%</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">HH%</th>
            <th class="label-caps !text-[8px] py-2 px-2 border-b border-bg-200 text-right">EV</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.lineupId"
            class="group hover:bg-bg-100/50 transition-colors"
          >
            <!-- Order -->
            <td class="py-2 px-3 border-b border-bg-200/40 display-num text-xs text-fg-500">
              {{ row.batting_order }}
            </td>
            <!-- Batter -->
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
            <!-- Odds -->
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
            <!-- HR% (or H% if hits mode) -->
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span
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
            <!-- Edge placeholder -->
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span class="label-bracket !text-[8px] opacity-50">pending</span>
            </td>
            <!-- BvP -->
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span v-if="row.bvp" class="display-num text-xs text-fg-600">
                {{ row.bvp.hr }}/{{ row.bvp.pa }}
              </span>
              <span v-else class="display-num text-xs text-fg-400">—</span>
            </td>
            <!-- Brl% -->
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span class="display-num text-xs text-fg-600">
                {{ row.barrel_pct != null ? Number(row.barrel_pct).toFixed(1) : '—' }}
              </span>
            </td>
            <!-- HH% -->
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span class="display-num text-xs text-fg-600">
                {{ row.hard_hit_pct != null ? Number(row.hard_hit_pct).toFixed(1) : '—' }}
              </span>
            </td>
            <!-- EV -->
            <td class="py-2 px-2 border-b border-bg-200/40 text-right">
              <span class="display-num text-xs text-fg-600">
                {{ row.ev_avg != null ? Number(row.ev_avg).toFixed(1) : '—' }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
